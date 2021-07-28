""" Script to Download Anime Episodes from Proxer.me """

import concurrent.futures as cf
import json
import logging
import os
import re
import sys
import time
import unicodedata
from configparser import ConfigParser
from datetime import datetime

import requests
from bs4 import BeautifulSoup, SoupStrainer
from cloudscraper import CloudScraper
from tqdm import tqdm as ProgressBar
from anticaptchaofficial.recaptchav2proxyless import *

### CONFIG Start ### define base Settings
CONFFILE = "settings.conf"
PARALLEL_DOWNLOADS = 3
STREAM_LANG = "engsub" 
BASE_URL = "https://proxer.me"
PATH = ""
SOLVERKEY = ""
### CONFIG END ###

HEADERS = requests.utils.default_headers()
HEADERS.update(
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/41.0.2227.0 Safari/537.36", })

SLASH = "\\" if os.name == "nt" else "/"
PWD = os.path.dirname(os.path.realpath(__file__)) + SLASH

LOGGER = logging.getLogger('plme.main')
LOG_FORMAT = "%(asctime)-15s | %(levelname)s | %(funcName)s() - Line %(lineno)d | %(message)s"
LOGGER.setLevel(logging.DEBUG)
STRMHDLR = logging.StreamHandler(stream=sys.stdout)
STRMHDLR.setLevel(logging.INFO)
STRMHDLR.setFormatter(logging.Formatter(LOG_FORMAT))
FLHDLR = logging.FileHandler(
    "error.log", mode="a", encoding="utf-8", delay=False)
FLHDLR.setLevel(logging.DEBUG)
FLHDLR.setFormatter(logging.Formatter(LOG_FORMAT))
LOGGER.addHandler(STRMHDLR)
LOGGER.addHandler(FLHDLR)
SCRAPER = CloudScraper()

SESSION = requests.Session()
EXECUTOR = cf.ThreadPoolExecutor(PARALLEL_DOWNLOADS)
config = ConfigParser()
config.read(CONFFILE)
# Define custom Errors
class NoURLError(Exception):
    pass
class CaptchaError(Exception):
    pass

def init_preps(retries=1):
    """ Function to log in and initiate the Download Process """
    
    try:  # safely try to read login credentials and log into proxer

        # Reading config file and overwrite default settings if set to another value
        
        user = config["LOGIN"]["USER"]
        passwd = config["LOGIN"]["PASS"]
        if config.has_option("SETTINGS","PARALLEL_DOWNLOADS"):
            PARALLEL_DOWNLOADS = config["SETTINGS"]["PARALLEL_DOWNLOADS"]
        if config.has_option("SETTINGS","STREAM_LANG"):
            STREAM_LANG = config["SETTINGS"]["STREAM_LANG"]
        if config.has_option("SETTINGS","BASEURL"):
            BASE_URL = config["SETTINGS"]["BASEURL"]      
        if config.has_option("SETTINGS","PATH"):
            PATH = config["SETTINGS"]["PATH"]  
        if config.has_option("SETTINGS","MAXRETRY"):
            MAX_RETRY = int(config["SETTINGS"]["MAXRETRY"])
        else:
            MAX_RETRY = 3
        if config.has_option("SETTINGS","PREFSOURCE"):
            PREF_SOURCE = config["SETTINGS"]["PREFSOURCE"]
        else:
            PREF_SOURCE = "proxer-stream"
        if config.has_option("SETTINGS","SOLVERKEY"):
            SOLVERKEY = config["SETTINGS"]["SOLVERKEY"]
        # use Cloudscraper to bypass Cloudflares Redirection Page
        mainPage = SCRAPER.get(f"{BASE_URL}/login")  # grab the main page
        # restrict to login related html using a strainer
        loginStrainer = SoupStrainer(id="login_form")
        # use the strainer to restrict parsing
        soup = BeautifulSoup(mainPage.content, "html.parser",
                             parse_only=loginStrainer)
        loginPath = soup.find("form")["action"]  # grab the login url
        # set credentials (remember is irrelevant, due to this being a singular session)
        creds = {"username": user, "password": passwd, "remember": 1}
        # hopefully logged in correctly
        SESSION.post(BASE_URL + loginPath, data=creds)
    except Exception as excp:
        LOGGER.exception(excp)
        LOGGER.warning("Something went wrong during Login!")
        if retries > MAX_RETRY:
            LOGGER.warning("Exiting...")
            exit(1)
        else:
            LOGGER.warning(f"Retring in 3 seconds... [{retries}/{MAX_RETRY}]")
            time.sleep(3)
            retries += 1
            init_preps(retries)

    inputurl = input(
        "Please enter the URL of the Anime you want to download: ")
    # inputurl = "https://proxer.me/info/6356"
    try:
        animeId = re.search(
            r"(?!https?:\/\/proxer\.me\/(info|watch)\/)\d+", inputurl).group()
    except:
        raise Exception("You entered an invalid url")
    # Get last episode
    episodesUrl = "https://proxer.me/info/"+animeId+"/list"
    mainPage = SESSION.get(episodesUrl)
    time.sleep(3)
    check_for_recaptcha(mainPage)
    episodesStrainer = SoupStrainer("div", id="contentList")
    epsiodesPage = BeautifulSoup(
        mainPage.content, "html.parser", parse_only=episodesStrainer)

    # Go to last page if pages exist
    try:
        pages = epsiodesPage.find_all("a", class_="menu")
        lastPageUrl = BASE_URL + pages[-1].get("href")
        lastPage = SESSION.get(lastPageUrl)
        time.sleep(3)
        check_for_recaptcha(lastPage)
        epsiodesPage = BeautifulSoup(
            lastPage.content, "html.parser", parse_only=episodesStrainer)
    except:
        pass
    # Read last episode first cell of last row
    episodeCountCell = epsiodesPage.find_all("tr").pop(-1).find("td")
    lastEpisode = int(episodeCountCell.get_text())
    eps = input(
        "Anime has "+str(lastEpisode)+" Episodes. Do you want to download all? y/n: ")
    if eps == "y":
        LOGGER.info("Downloading all available episodes..")
        lasteps = lastEpisode
        firsteps = 1
    elif eps == "n":
        firsteps = input(
            "Enter first episode to download:"
        )
        lasteps = input(
            "Enter last episode to download:"
        )
    else:
        LOGGER.warning("icorrect input. Exiting..")
        exit(1)
    # Get anime name
    animeName = epsiodesPage.find(
        "span", id="listTitle" + str(lastEpisode))
    animeName = animeName.text.replace(f" Episode {lastEpisode}", "")
    animeName = sanitizeName(animeName)

    # Create download directory
    if PATH is None:
        LOGGER.warning("path doesnt set, using default")
        animedir = f"{PWD}{animeName}{SLASH}"
    else: 
        LOGGER.warning("path is set to custom")
        animedir = f"{PATH}/{animeName}{SLASH}"
    if not os.path.exists(animedir):
        os.mkdir(animedir)
    os.chdir(animedir)

    episodeDownloaders = []

    for episodeNum in range(int(firsteps), int(lasteps) + 1):
        episodeUrl = f"{BASE_URL}/watch/{animeId}/{episodeNum}/{STREAM_LANG}"
        LOGGER.debug(episodeUrl)
        LOGGER.debug(f"Creating Worker for Episode {episodeNum}")
        episodeDownloaders.append(EXECUTOR.submit(
            retrieve_source, episodeUrl, animeName, episodeNum, PREF_SOURCE))

    # check for thread status
    for episodeDownloader in cf.as_completed(episodeDownloaders):
        try:
            video = episodeDownloader.done()  # cf equivalent of threading.Thread.join()
            # LOGGER.debug(f"Worker for Episode {episodeNum} returned: {video}")
        except Exception as excp:
            LOGGER.exception(
                f"{episodeUrl} has thrown Exception:\n{excp}")

def sanitizeName(value):
    value = str(value)
    value = unicodedata.normalize('NFKD', value).encode(
        'ascii', 'ignore').decode('ascii')
    return re.sub(r'[^\w\s-]', '', value)

def check_for_recaptcha(page):
    if "Captcha Eingabe" in page.text:
        if config.has_option("SETTINGS","SOLVERKEY"):
            SOLVERKEY = config["SETTINGS"]["SOLVERKEY"]
        else:
            print("no captcha solver api key")
            exit(1)
        print("trying anticaptcha")
        
        captchaStrainer = SoupStrainer("div", id="captcha")
        captchaSoup = BeautifulSoup(page.content, "html.parser",
                             parse_only=captchaStrainer)
        captcha = captchaSoup.find("div")["data-sitekey"]
        LOGGER.warning("Captcha-ID: "+captcha)
        solver = recaptchaV2Proxyless()
        solver.set_verbose(1)
        solver.set_key(str(SOLVERKEY))
        solver.set_website_url(page.url)
        solver.set_website_key(captcha)
        g_response = solver.solve_and_return_solution()
        if g_response != 0:
            print("g-response: "+g_response)
            capt = {"response": g_response}
            x = SESSION.post("https://proxer.me/components/com_proxer/misc/captcha/recaptcha.php", data=capt)
            LOGGER.info("Captcha solved. Retrying episode")
            return "true"
        else:
            print("task finished with error "+solver.error_code)
            LOGGER.exception(
                "The site returns a recaptcha. Please try again later or switch your public ip address")
            exit(1)

def retrieve_source(episodeUrl, animeName, episodenum, PREF_SOURCE):
    """ Function to make all the Magic happen, parses the streamhoster url [Proxer] and parses the video url """
    try:  # if anything fails in here, it's prolly the captcha
        #LOGGER.info(f"{episodeurl}, {name}, {episodenum}")
        streamhosterurl = None
        # grab the specific episode
        time.sleep(3)
        episodePage = SESSION.get(episodeUrl, timeout=30)
        time.sleep(3)
        if check_for_recaptcha(episodePage) == "true":
            episodePage = SESSION.get(episodeUrl, timeout=30)

        streamsString = re.search(r"var streams = (.*\]);", episodePage.text)

        if streamsString is None:
            raise NoURLError

        streams = json.loads(streamsString.group(1))

        for stream in streams:
            if stream['type'] == PREF_SOURCE:
                streamhosterurl = stream['replace'].replace(
                    "#", stream['code'])
                if PREF_SOURCE == "mp4upload":
                    streamhosterurl = "http:"+streamhosterurl
        if streamhosterurl is None: 
            if PREF_SOURCE == "proxer-stream":
                FALL_SOURCE == "mp4upload"
            elif PREF_SOURCE == "mp4upload":
                FALL_SOURCE == "proxer-stream"
            for stream in streams:
                if stream['type'] == FALL_SOURCE:
                    streamhosterurl = stream['replace'].replace(
                        "#", stream['code'])
            if FALL_SOURCE == "mp4upload":
                streamhosterurl = "http:"+streamhosterurl

            # if stream['type'] == "proxer-stream":
            #     streamhosterurl = stream['replace'].replace(
            #         "#", stream['code'])

        LOGGER.info(f"Streamhoster: {streamhosterurl}")

        if streamhosterurl == None:
            raise NoURLError

        # grabbing the page where the video is embedded in
        streamPage = SESSION.get(streamhosterurl, timeout=30)
        check_for_recaptcha(streamPage)

        # restrict to login related html using a strainer
        streamStrainer = SoupStrainer("source")
        videoSourceTag = BeautifulSoup(
            streamPage.content, "html.parser", parse_only=streamStrainer)

        streamurl = videoSourceTag.find("source").get("src")

        episodename = f"{os.getcwd()}{SLASH}{animeName} - Episode {episodenum}.mp4"

        if streamurl == "":
            raise NoURLError

        LOGGER.info(f"Streamurl: {streamurl}")
        download_file(episodename, streamurl)

    except Exception as excp:
        LOGGER.exception(f"{excp}")

def download_file(targetFilePath, srcurl):
    """ Function to Downloadad and verify downloaded Files """
    videoStream = SESSION.get(srcurl, stream=True)
    fileSize = int(videoStream.headers['content-length'] or 0)

    if os.path.exists(targetFilePath) and os.path.getsize(targetFilePath) < fileSize:
        os.remove(targetFilePath)

    LOGGER.debug(f"Downloading {srcurl} as {targetFilePath}")
    with open(targetFilePath, "wb") as videoFile:
        if fileSize == 0:
            videoFile.write(videoStream.content)
        else:
            filename = targetFilePath.split(SLASH)[-1]
            progressBar = ProgressBar(
                total=fileSize, unit_scale=True, desc=filename, unit="bytes")
            progressBar.get_lock()

            for chunk in videoStream.iter_content(4096):
                videoFile.write(chunk)
                progressBar.update(len(chunk))

            progressBar.close()

def __main__():
    """ MAIN """
    init_preps()

if __name__ == "__main__":  # main guard
    __main__()