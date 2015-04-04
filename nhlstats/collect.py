# -*- coding: utf-8 -*-
"""
Collect concerns itself with the screen scraping functionality.
"""

# TODO(jkelly) Ensure team names are consistent

import os
import re
import pytz
import json
import logging
import urllib2
import datetime
from hashlib import sha1
from lxml.html import parse

from .version import __version__
from .models import Season

logger = logging.getLogger(__name__)
logger.debug('Loading {} ver {}'.format(__name__, __version__))


class UnexpectedPageContents(Exception):
    """
    Used for when the page is retrieved, but the contents are
    unexpected
    """
    pass


class Collector(object):
    """
    Base collector object implementing generic functionality.  New
    collector classes should derive from this, but it shouldn't be
    used directly to gather data, instead collectors such as
    HTMLCollector and JSONCollector which derive from this
    should be used.
    """
    def __init__(self, url, cache_dir='cache'):
        self.url = url
        self.cache_dir = cache_dir

    def check_season_type(self, season_type):
        """
        Useful for collectors dealing with games, there are three game
        types, preseason, regular, and postseason. Verify our season_type
        is known.
        """
        if season_type not in Season.get_season_types():
            raise ValueError('Season type of {} is unknown'.format(season_type))

    def check_season(self, season):
        """
        Useful for season based collectors, this checks the seasons of
        the correct format.
        """
        if not re.match('[0-9]{8}', season):
            raise ValueError('Season "{}" is not of the correct format, which is two directly concatonated YYYY values, ie 20132014'.format(season))

    def convert_datetime_to_utc(self, date, tz=pytz.timezone('US/Eastern')):
        """
        Given a datetime object, convert it to utc from tz (defaults to US/Eastern)
        """
        return tz.localize(date).astimezone(pytz.timezone('UTC'))

    def url_to_filename(self, url):
        hash_file = sha1(url).hexdigest() + '.html'
        return os.path.join(self.cache_dir, hash_file)

    def store_cache(self, url, content):
        """
        Cache a local copy of the file.
        """

        # If the cache directory does not exist, make one.
        if not os.path.isdir(self.cache_dir):
            os.makedirs(self.cache_dir)

        local_path = self.url_to_filename(url)

        logger.debug('Storing {} in cache as {}'.format(url, local_path))

        with open(local_path, 'wb') as fp:
            fp.write(content)

    def load_from_cache(self, url):
        """
        If we do not have a cached version,
        get one. Return pointer to that.
        """
        local_path = self.url_to_filename(url)
        if not os.path.exists(local_path):
            try:
                logger.debug('Unable to load {} from cache ({}), downloading.'.format(url, local_path))
                self.store_cache(url, urllib2.urlopen(url).read())
            except (urllib2.HTTPError, urllib2.URLError):
                logger.error('Unable to load page at {}'.format(url))
                raise
        else:
            logger.debug('Loaded {} from cache ({})'.format(
                url, local_path
            ))

        return local_path

    def parse(self, data):
        """
        This should be implemented by classes that inherit from us.
        data will be an lxml.etree.parse object.
        """
        return

    def verify(self, data):
        """
        This optional method is for verifying the page contents are as
        expected. Raise UnexpectedPageContents if the contents of data
        are unexpected.
        """
        return


class HTMLCollector(Collector):
    """
    The HTML Collector class implements collection by scraping HTML
    page.
    """
    def scrape(self):
        data = parse(self.load_from_cache(self.url)).getroot()

        # The parse functionality must be implemented by
        # our sub.  We currently aren't
        self.verify(data)
        return self.parse(data)


class JSONCollector(Collector):
    """
    The JSON Collector class implements collection by scraping
    a JSON page.
    """
    def scrape(self):
        with open(self.load_from_cache(self.url)) as fp:
            data = json.load(fp)

            self.verify(data)
            return self.parse(data)


class NHLArena(HTMLCollector):
    """
    This retrieves information on an arena from the NHL
    """
    def __init__(self, team, url='http://www.nhl.com/ice/ajax/teammapmodal?team={}'):
        super(NHLArena, self).__init__(url.format(team))

    def parse(self, data):
        # long re is long
        info = re.compile('<div style="font-weight: normal; font-size: 12px; font-family: arial,helvetica;"><b>(?P<name>[\w\s\-\,\.\&À-ú]+)</b><br />(?P<street>[\w\s\-\,\.\&À-ú]+)<br />(?P<city>[\w\s\-\,\.\&À-ú]+), (?P<state>[A-Z]{2}), (?P<country>[\w\s\-\,\.\&À-ú]+)  (?P<postal_code>[\w\s\-\,\.\&]+)<br /></div>')

        return re.search(info, data.text_content()).groupdict()


# This is a poor name, but better than "Seasons" I suppose
class NHLDivisions(HTMLCollector):
    """
    This sets up the scaoffold for an NHL season,
    scraping the conferences, divisions, teams.
    """
    def __init__(self, season=None, url='http://www.nhl.com/ice/standings.htm?season={}&type=DIV'):
        if season:
            self.check_season(season)
        else:
            season = ''

        self.season = season

        super(NHLDivisions, self).__init__(url.format(season))

    def parse(self, data):
        conferenceText = 'conferenceHeader'
        i = 0

        # Pick up normal teams
        teams = [item.text for item in data.xpath('//td[@style="text-align:left;"]/a[2]')]

        # Pick up teams that don't exist anymore (they're not links to team pages)
        teams.extend([item.text for item in data.xpath('//span[@class="team"]')])

        results = {}

        for team in teams:
            i += 1
            # An exception to our policy of using '{}'.format(foo) occurs here, as
            # for some reason this throws unicode errors, in a way I don't care
            # to further investigate beyond trying expected fixes.
            division = data.xpath('//*[text()="%s"]/parent::td/parent::tr/parent::tbody/preceding-sibling::thead/tr[1]/th[@abbr="DIV"]' % team)[0].text
            conference = [item.get('class').replace(conferenceText, '') for item in data.xpath('//*[text()="%s"]/parent::td/parent::tr/parent::tbody/parent::table/preceding-sibling::div[starts-with(@class, "%s")]' % (team, conferenceText))][-1]

            if conference not in results:
                results[conference] = {}

            if division not in results[conference]:
                results[conference][division] = []

            results[conference][division].append(team)

        return results

    def verify(self, data):
        seasonBlocks = data.xpath('//div[@class="sectionHeader"]/h3')
        if self.season:
            expectedSeason = self.season[:4] + '\-' + self.season[4:]
        else:
            expectedSeason = '[0-9]{4}\-[0-9]{4}'

        if not (seasonBlocks and re.match(expectedSeason, seasonBlocks[0].text.strip())):
            raise UnexpectedPageContents('Expected {} season, found {}'.format(expectedSeason, seasonBlocks[0].text.strip()))


class NHLSchedule(HTMLCollector):
    """
    Scrapes the season schedule from the NHL, careful to only include
    games with NHL teams (they'll have olympic games in there, for
    instance)
    """
    SCHEDULE_ROW_XPATH = '//table[@class="data schedTbl"]/tbody/tr'

    def __init__(self, season, season_type='regular', url='http://www.nhl.com/ice/schedulebyseason.htm?season={}&gameType={}&team=&network=&venue='):
        self.check_season(season)
        self.check_season_type(season_type)
        self.season = season
        self.season_type = season_type

        super(NHLSchedule, self).__init__(url.format(season, Season.get_season_type_id(season_type)))

    def parse(self, data):
        games = []

        # Iterate over the schedule rows
        for row in data.xpath(self.SCHEDULE_ROW_XPATH):
            game = self.parse_row(row)

            if game:
                games.append(game)

        return games

    def parse_row(self, row):
        teams = [item.text for item in row.xpath('td[@class="team"]/div[@class="teamName"]/a|td[@class="team"]/div[@class="teamName"]') if item.text]

        # If we don't have two teams, we must be in some header row
        if not len(teams) == 2:
            return
        # If we have a non-breaking space, it's an Olympic team.
        elif [team for team in teams if u'\xa0' in team]:
            return
        else:
            date = row.xpath('td[@class="date"]/div[@class="skedStartDateSite"]')[0].text
            startDate = datetime.datetime.strptime(date.strip(), '%a %b %d, %Y').date()

            # If there isn't yet a known time for the game, that's okay, let's just
            # leave it as None, we'll be checking again.
            if 'TBD' not in row.xpath('td[@class="time"]')[0].text_content():
                time = row.xpath('td[@class="time"]/div[@class="skedStartTimeEST"]')[0].text
                localTime = datetime.datetime.strptime(date + ' ' + time.replace('ET', '').strip(),  '%a %b %d, %Y %I:%M %p')
                startTime = self.convert_datetime_to_utc(localTime).time()
            else:
                startTime = None

            return {
                'season': self.season,
                'date': startDate,
                'time': startTime,
                'home': teams[1],
                'visitor': teams[0],
                'start': startDate,
                'type': self.season_type
            }

    def verify(self, data):
        if not data.xpath('//table[@class="data schedTbl"]/tbody/tr'):
            raise UnexpectedPageContents('No schedule block found on {} page.'.format(self.season))


class NHLGameReports(NHLSchedule):
    """
    Collects GameReport ids from an NHL Schedule
    """
    # Will match the type to group 1 and the game id to group 2
    GAME_ID_REGEX = re.compile('http://www.nhl.com/gamecenter/en/(recap|preview)\?id=[0-9]{4}([0-9]+)')

    def parse(self, data):
        games = []

        # Iterate over the schedule rows
        for row in data.xpath(self.SCHEDULE_ROW_XPATH):
            game = self.parse_row(row)

            if game:
                scheduleLinks = row.xpath('td[@class="skedLinks"]/a')

                idNum = None
                for link in scheduleLinks:
                    match = re.match(self.GAME_ID_REGEX, link.attrib['href'])
                    if match:
                        idNum = match.group(2)
                        break

                if idNum:
                    game['reportid'] = idNum
                    games.append(game)

        return games


class NHLTeams(HTMLCollector):
    """
    Unfortunately because the NHL is happy to mix in Olympic
    games and the like with their schedule, we must populate
    the teams separately. We do so by looking at pre-season
    games, which presumably only contain NHL teams.
    """
    def __init__(self, url='http://www.nhl.com/ice/teams.htm'):
        super(NHLTeams, self).__init__(url)

    def parse(self, data):
        retrieved_data = []

        # Start from index 1, as 0 is the NHL logo.
        for team in data.cssselect('div.teamCard'):
            team_data = {
                'division': team.getparent().get('class'),
                'city': team.cssselect('span.teamPlace')[0].text_content(),
                'name': team.cssselect('span.teamCommon')[0].text_content(),
                'url': team.cssselect('div.teamLogo>a')[0].attrib['href'],
                'acronym': team.values()[0].split()[-1].upper()
            }

            # For some reason these teamCards show up twice, so check
            if team_data not in retrieved_data:
                retrieved_data.append(team_data)

        return retrieved_data


class NHLRoster(HTMLCollector):
    """
    Gets an NHL Roster for a team
    """
    def __init__(self, team, url='http://{}.nhl.com/club/roster.htm'):
        self.teamDomain = 'http://{}.nhl.com'.format(team)
        super(NHLRoster, self).__init__(url.format(team))

    def parse(self, data):
        players = []
        for row in data.xpath('//table[@class="data"]/tr[@class!="hdr"]'):
            if row.xpath("td[@colspan=7]"):
                continue

            player = {
                'number': row.xpath('td/span[@class="sweaterNo"]')[0].text,
                'name': row.xpath('td/nobr/a')[0].text,
                'url': self.teamDomain + row.xpath('td/nobr/a')[0].attrib['href'],
                'height': row.xpath('td[3]')[0].text,
                'weight': row.xpath('td[4]')[0].text,
                'dob': row.xpath('td[5]')[0].text,
                'hometown': row.xpath('td[7]')[0].text
            }

            players.append(player)

        return players

    def verify(self, data):
        rosterHeadersPlayerName = data.xpath('//table[@class="data"]/tr[@class="hdr"]/td[2]/a')
        if not (len(rosterHeadersPlayerName) == 3 and 'Name' in rosterHeadersPlayerName[0].text):
            raise UnexpectedPageContents('Unable to locate roster header as expected on {}'.format(data.base_url))


class NHLEventLocations(JSONCollector):
    def __init__(self, season, reportid, url='http://live.nhl.com/GameData/{}/{}/PlayByPlay.json'):
        self.season = season
        self.reportid = reportid
        super(NHLEventLocations, self).__init__(url.format(season, season[:4] + reportid))

    def parse(self, data):
        return {
            'plays': data['data']['game']['plays']['play'],
            'away': data['data']['game']['awayteamname'],
            'home': data['data']['game']['hometeamname']
        }

    def verify(self, data):
        if 'data' not in data:
            raise UnexpectedPageContents('data section of JSON does not exist on {}'.format(data.base_url))


class NHLEvents(HTMLCollector):
    def __init__(self, season, reportid, url='http://www.nhl.com/scores/htmlreports/{}/PL{}.HTM'):
        self.season = season
        self.reportid = reportid
        super(NHLEvents, self).__init__(url.format(season, reportid))

    def parse(self, data):
        events = []
        for row in data.xpath('//tr[@class="evenColor"]'):
            rowdata = row.xpath('td')
            awayice = [cell for cell in rowdata[6].xpath('table/tr/td') if u'\xa0' not in cell.text_content()]
            homeice = [cell for cell in rowdata[7].xpath('table/tr/td') if u'\xa0' not in cell.text_content()]

            events.append({
                'period': rowdata[1].text,
                'time': rowdata[3].text,
                'event': rowdata[4].text,
                'description': rowdata[5].text,
                'away': [],
                'home': []
            })

            for player in awayice:
                events[-1]['away'].append({
                    'player': player.xpath('table/tr/td')[0].text_content().strip(),
                    'position': player.xpath('table/tr/td')[1].text_content().strip()
                })

            for player in homeice:
                events[-1]['home'].append({
                    'player': player.xpath('table/tr/td')[0].text_content().strip(),
                    'position': player.xpath('table/tr/td')[1].text_content().strip()
                })

        return events

    def verify(self, data):
        header = '\r\n#\r\nPer\r\nStr\r\nTime:ElapsedGame\r\nEvent\r\nDescription\r\nTOR On Ice\r\nWSH On Ice\r\n'
        headerSearch = [e for e in data.xpath('//tr') if e.text_content() == header]

        if headerSearch and not (data.xpath('//table[@id="Visitor"]') and data.xpath('//table[@id="Home"]')):
            raise UnexpectedPageContents('Unable to locate events page as expected on {}'.format(data.base_url))
