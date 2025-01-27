import os
import re
import time
import string
import thread
import threading
import urllib
import copy
from urllib2 import HTTPError
from datetime import datetime
from lxml import etree

API_KEY = ''
PLEX_HOST = ''

#this is from https://github.com/plexinc-agents/PlexThemeMusic.bundle/blob/master/Contents/Code/__init__.py
THEME_URL = 'http://tvthemes.plexapp.com/%s.mp3'
LINK_REGEX = r"https?:\/\/\w+.\w+(?:\/?\w+)? \[([^\]]+)\]"

def ValidatePrefs():
    pass


def Start():
    Log("Shoko metata agent started")
    HTTP.Headers['Accept'] = 'application/json'
    HTTP.CacheTime = 0.1 #cache, can you please go away, typically we will be requesting LOCALLY. HTTP.CacheTime
    ValidatePrefs()


def GetApiKey():
    global API_KEY

    if not API_KEY:
        data = '{"user":"%s", "pass":"%s", "device":"%s"}' % (
            Prefs['Username'], Prefs['Password'] if Prefs['Password'] != None else '', 'Shoko Metadata For Plex')
        resp = HttpPost('api/auth', data)['apikey']
        Log.Debug("Got API KEY: %s" % resp)
        API_KEY = resp
        return resp

    return API_KEY


def HttpPost(url, postdata):
    myheaders = {'Content-Type': 'application/json'}
    return JSON.ObjectFromString(
        HTTP.Request('http://%s:%s/%s' % (Prefs['Hostname'], Prefs['Port'], url), headers=myheaders,
                     data=postdata).content)


def HttpReq(url, authenticate=True, retry=True):
    global API_KEY
    Log("Requesting: %s" % url)
    api_string = ''
    if authenticate:
        api_string = '&apikey=%s' % GetApiKey()

    try:
        return JSON.ObjectFromString(
            HTTP.Request('http://%s:%s/%s%s' % (Prefs['Hostname'], Prefs['Port'], url, api_string)).content)
    except Exception, e:
        if not retry:
            raise e

        API_KEY = ''
        return HttpReq(url, authenticate, False)
        


class ShokoCommonAgent:
    def Search(self, results, media, lang, manual, movie):
        name = ( media.title if movie else media.show )

        # http://127.0.0.1:8111/api/serie/search?query=Clannad&level=1&apikey=d422dfd2-bdc3-4219-b3bb-08b85aa65579

        if movie:
            if media.filename:
                filename = os.path.basename(urllib.unquote(media.filename))

                episode_data = HttpReq("api/ep/getbyfilename?filename=%s" % (urllib.quote(filename.encode('utf8'))))
                movie_data = HttpReq("api/serie/fromep?id=%s" % (episode_data['id']))

                score = 100 if movie_data['name'] == name else 85  # TODO: Improve this to respect synonyms./
                title = movie_data['name'] + ' - ' + episode_data['name']
                meta = MetadataSearchResult('%s' % (episode_data['id']), title, try_get(episode_data, 'year', None), score, lang)
                results.Append(meta)

            else: # For manual searches
                prelimresults = HttpReq("api/serie/search?query=%s&level=%d&fuzzy=%d&ismovie=1" % (urllib.quote(name.encode('utf8')), 2, Prefs['Fuzzy']))

                for result in prelimresults:
                    for episode in result['eps']:
                        title = result['name'] + ' - ' + episode['name']
                        if title == name: score = 100 # Check if full name matches (Series name + episode name)
                        elif result['name'] == name: score = 90 # Check if series name matches
                        else: score = 80
                        meta = MetadataSearchResult('%s' % (episode['id']), title, try_get(episode, 'year', None), score, lang)
                        results.Append(meta)

        else:
            prelimresults = HttpReq("api/serie/search?query=%s&level=%d&fuzzy=%d" % (urllib.quote(name.encode('utf8')), 1, Prefs['Fuzzy']))

            for result in prelimresults:
                #for result in group['series']:
                score = 100 if result['name'] == name else 85  # TODO: Improve this to respect synonyms./
                meta = MetadataSearchResult('%s' % result['id'], result['name'], try_get(result, 'year', None), score, lang)
                results.Append(meta)

                # results.Sort('score', descending=True)

    def Update(self, metadata, media, lang, force, movie):
        Log("update(%s)" % metadata.id)
        aid = metadata.id
        # title = media.name
        # http://127.0.0.1:8111/api/ep/getbyfilename?apikey=d422dfd2-bdc3-4219-b3bb-08b85aa65579&filename=%5Bjoseole99%5D%20Clannad%20-%2001%20(1280x720%20Blu-ray%20H264)%20%5B8E128DF5%5D.mkv

        # episode_data = HttpReq("api/ep/getbyfilename?apikey=%s&filename=%s" % (GetApiKey(), urllib.quote(media.filename)))


        flags = 0
        flags = flags | Prefs['hideMiscTags']       << 0 #0b00001 : Hide AniDB Internal Tags
        flags = flags | Prefs['hideArtTags']        << 1 #0b00010 : Hide Art Style Tags
        flags = flags | Prefs['hideSourceTags']     << 2 #0b00100 : Hide Source Work Tags
        flags = flags | Prefs['hideUsefulMiscTags'] << 3 #0b01000 : Hide Useful Miscellaneous Tags
        flags = flags | Prefs['hideSpoilerTags']    << 4 #0b10000 : Hide Plot Spoiler Tags

        if movie:
            series = HttpReq("api/serie/fromep?id=%s&level=3&allpics=1&tagfilter=%d" % (aid, flags))
            movie_episode_data = HttpReq("api/ep?id=%s" % (aid))
            aid = try_get(series, 'id', None)
            if not aid:
                Log('Error! Series not found.')
                return

            if movie_episode_data['name'] == 'Complete Movie':
                movie_name = series['name']
                movie_sort_name = series['name']
            else:
                movie_name = series['name'] + ' - ' + movie_episode_data['name']
                movie_sort_name = series['name'] + ' - ' + str(movie_episode_data['epnumber']).zfill(3)

            metadata.summary = summary_sanitizer(try_get(series, 'summary'))
            metadata.title = movie_name
            metadata.title_sort = movie_sort_name
            metadata.rating = float(movie_episode_data['rating'])
            year = try_get(movie_episode_data, "year", try_get(series, "year", None))

            if year:
                metadata.year = int(year)

            collections = []
            if series['localsize'] > 1:
                collections.append(series['name'])

        else:
            series = HttpReq("api/serie?id=%s&level=3&allpics=1&tagfilter=%d" % (aid, flags))

            metadata.summary = summary_sanitizer(try_get(series, 'summary'))
            metadata.title = series['name']
            metadata.rating = float(series['rating'])

            groupinfo = HttpReq("api/serie/groups?id=%s&level=2" % aid);
            collections = []
            for group in groupinfo:
                if (len(group['series']) > 1):
                    collections.append(group['name'])

        metadata.collections = collections

        tags = []
        for tag in try_get(series, 'tags', []):
            tags.append(tag)

        metadata.genres = tags

        self.metadata_add(metadata.banners, try_get(series['art'], 'banner', []))
        self.metadata_add(metadata.posters, try_get(series['art'], 'thumb', []))
        self.metadata_add(metadata.art, try_get(series['art'], 'fanart', []))


        ### Generate general content ratings.
        ### VERY rough approximation to: https://www.healthychildren.org/English/family-life/Media/Pages/TV-Ratings-A-Guide-for-Parents.aspx

        if Prefs["Ratings"]:
            if 'Kodomo' in tags:
                metadata.content_rating = 'TV-Y'

            if 'Mina' in tags:
                metadata.content_rating = 'TV-G'

            if 'Shoujo' in tags:
                metadata.content_rating = 'TV-14'

            if 'Shounen' in tags:
                metadata.content_rating = 'TV-14'

            if 'Josei' in tags:
                metadata.content_rating = 'TV-14'

            if 'Seinen' in tags:
                metadata.content_rating = 'TV-MA'

            if 'Mature' in tags:
                metadata.content_rating = 'TV-MA'

            if '18 Restricted' in tags:
                metadata.content_rating = 'TV-R'

            Log('Assumed tv rating to be: %s' % metadata.content_rating)

        airdate = try_get(series, 'air', '1/01/0001 12:00:00 AM')
        if airdate != '1/01/0001 12:00:00 AM' and airdate != '0001-01-01':
            metadata.originally_available_at = datetime.strptime(airdate, "%Y-%m-%d").date()

        metadata.roles.clear()
        for role in try_get(series, 'roles', []):
            meta_role = metadata.roles.new()
            Log(role['character'])
            meta_role.name = role['staff']
            meta_role.role = role['character']
            meta_role.photo = "http://{host}:{port}{relativeURL}".format(host=Prefs['Hostname'], port=Prefs['Port'], relativeURL=role['staff_image'])

        # Add studio information
        # Plex does not support multiple studios, we take the first studio found.
        cast_v3 = HttpReq("api/v3/series/%s/Cast?" % aid)
        for element in cast_v3:
            if element['RoleName'] == "Studio":
                metadata.studio = element['Staff']['Name']
                break

        # If there is no "Animation Work" on AniDB we use "Work"
        if metadata.studio == None:
            for element in cast_v3:
                if element['RoleDetails'] == "Work":
                    metadata.studio = element['Staff']['Name']
                    break


        if not movie:
            for ep in series['eps']:
                if ep['eptype'] not in ["Episode", "Special", "Credits", "Trailer"]:
                    continue

                if ep['eptype'] == "Episode": season = 1
                elif ep['eptype'] == "Special": season = 0
                elif ep['eptype'] == "Credits": season = -1
                elif ep['eptype'] == "Trailer": season = -2
                if not Prefs['SingleSeasonOrdering']:
                    try:
                        season = int(ep['season'].split('x')[0])
                        if season <= 0 and ep['eptype'] == 'Episode': season = 1
                        elif season > 0 and ep['eptype'] == 'Special': season = 0
                    except:
                        pass

                episodeObj = metadata.seasons[season].episodes[ep['epnumber']]
                episodeObj.title = ep['name']
                if (ep['summary'] != "Episode Overview not Available"): 
                    episodeObj.summary = summary_sanitizer(ep['summary'])
                Log("" + str(ep['epnumber']) + ": " + ep['summary'])

                airdate = try_get(ep, 'air', '1/01/0001 12:00:00 AM')

                if airdate != '1/01/0001 12:00:00 AM' and airdate != '0001-01-01':
                    episodeObj.originally_available_at = datetime.strptime(airdate, "%Y-%m-%d").date()

                if len(ep['art']['thumb']) and Prefs['customThumbs']:
                    self.metadata_add(episodeObj.thumbs, ep['art']['thumb'])

            links = HttpReq("api/links/serie?id=%s" % aid)

            #adapted from: https://github.com/plexinc-agents/PlexThemeMusic.bundle/blob/fb5c77a60c925dcfd60e75a945244e07ee009e7c/Contents/Code/__init__.py#L41-L45
            if Prefs["themeMusic"]:
                for tid in links["tvdb"]:
                    if THEME_URL % tid not in metadata.themes:
                        try:
                            metadata.themes[THEME_URL % tid] = Proxy.Media(HTTP.Request(THEME_URL % tid))
                            Log("added: %s" % THEME_URL % tid)
                        except:
                            Log("error adding music, probably not found")

    def metadata_add(self, meta, images):
        valid = list()
        
        for art in images:
            try:
                if 'support/plex_404.png' in art['url']:
                    continue
                if 'Static/plex_404.png' in art['url']:
                    continue
                if ':' in art['url']:
                    urlparts = urllib.parse.urlparse(art['url'])
                    art['url'] = art['url'].replace("{scheme}://{host}:{port}/".format(scheme=urlparts.scheme, host=urlparts.hostname, port=urlparts.port), '')
                url = "http://{host}:{port}{relativeURL}".format(host=Prefs['Hostname'], port=Prefs['Port'], relativeURL=art['url'])
                idx = try_get(art, 'index', 0)
                Log("[metadata_add] :: Adding metadata %s (index %d)" % (url, idx))
                meta[art['url']] = Proxy.Media(HTTP.Request(url).content, idx)
                valid.append(art['url'])
            except Exception as e:
                Log("[metadata_add] :: Invalid URL given (%s), skipping" % art['url'])
                Log(e)

        meta.validate_keys(valid)

        for key in meta.keys():
            if (key not in valid):
                del meta[key]

def summary_sanitizer(summary):
    if Prefs["synposisCleanLinks"]:
        summary = re.sub(LINK_REGEX, r'\1', summary)                                           # Replace links
    if Prefs["synposisCleanMiscLines"]:
        summary = re.sub(r'^(\*|--|~) .*',              "",      summary, flags=re.MULTILINE)  # Remove the line if it starts with ('* ' / '-- ' / '~ ')
    if Prefs["synposisRemoveSummary"]:
        summary = re.sub(r'\n(Source|Note|Summary):.*', "",      summary, flags=re.DOTALL)     # Remove all lines after this is seen
    if Prefs["synposisCleanMultiEmptyLines"]:
        summary = re.sub(r'\n\n+',                      r'\n\n', summary, flags=re.DOTALL)     # Condense multiple empty lines
    return summary.strip(" \n")

def try_get(arr, idx, default=""):
    try:
        return arr[idx]
    except:
        return default


class ShokoTVAgent(Agent.TV_Shows, ShokoCommonAgent):
    name, primary_provider, fallback_agent, contributes_to, accepts_from = (
        'ShokoTV', True, False, ['com.plexapp.agents.hama'],
        ['com.plexapp.agents.localmedia'])  # , 'com.plexapp.agents.opensubtitles'
    languages = [Locale.Language.English, 'fr', 'zh', 'sv', 'no', 'da', 'fi', 'nl', 'de', 'it', 'es', 'pl', 'hu', 'el',
                 'tr', 'ru', 'he', 'ja', 'pt', 'cs', 'ko', 'sl', 'hr']

    def search(self, results, media, lang, manual): self.Search(results, media, lang, manual, False)

    def update(self, metadata, media, lang, force): self.Update(metadata, media, lang, force, False)


class ShokoMovieAgent(Agent.Movies, ShokoCommonAgent):
    name, primary_provider, fallback_agent, contributes_to, languages, accepts_from = (
        'ShokoMovies', True, False, ['com.plexapp.agents.hama'], [Locale.Language.English, 'fr', 'zh', 'sv', 'no', 'da', 'fi', 'nl', 'de', 'it', 'es', 'pl', 'hu', 'el', 'tr', 'ru', 'he', 'ja', 'pt', 'cs', 'ko', 'sl', 'hr'],
        ['com.plexapp.agents.localmedia'])  # , 'com.plexapp.agents.opensubtitles'

    def search(self, results, media, lang, manual): self.Search(results, media, lang, manual, True)

    def update(self, metadata, media, lang, force): self.Update(metadata, media, lang, force, True)
