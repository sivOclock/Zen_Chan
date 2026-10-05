"""Built-in knowledge: categories, popular domains, feeds, search engines,
platform owners, ad-profile segments and emotion lexicons.

Everything here is plain data so it is easy to audit and extend.
"""

from __future__ import annotations

import re

# kind: productive | leisure | neutral | utility
CATEGORIES: dict[str, dict] = {
    "Programming & Dev": {"kind": "productive", "color": "#39ff14",
        "desc": "software development, writing code, programming languages, APIs, debugging, developer tools, open source repositories, stack traces"},
    "AI Tools": {"kind": "productive", "color": "#00e5ff",
        "desc": "AI chatbots and assistants, large language models, prompts, machine learning tools, image generation"},
    "Work & Productivity": {"kind": "productive", "color": "#7cfc00",
        "desc": "work documents, spreadsheets, project management, tasks, meetings, calendars, office tools, dashboards"},
    "Learning & Education": {"kind": "productive", "color": "#adff2f",
        "desc": "online courses, tutorials, lectures, studying, homework, university, exam preparation, learning a skill"},
    "Research & Reference": {"kind": "productive", "color": "#9acd32",
        "desc": "encyclopedia articles, dictionaries, academic papers, documentation, reference material, translations"},
    "Science": {"kind": "productive", "color": "#66cdaa",
        "desc": "physics, biology, chemistry, astronomy and space, scientific discoveries and research"},
    "Jobs & Career": {"kind": "productive", "color": "#20b2aa",
        "desc": "job search, job listings, resumes, interviews, careers, hiring, salaries"},
    "News & Politics": {"kind": "neutral", "color": "#ffb000",
        "desc": "breaking news, world news, politics, elections, government, current events, opinion columns"},
    "Tech News & Gadgets": {"kind": "neutral", "color": "#ffd700",
        "desc": "smartphones, laptops, gadget reviews, consumer electronics, technology industry news"},
    "Reading & Blogs": {"kind": "neutral", "color": "#f0e68c",
        "desc": "long-form articles, essays, blogs, newsletters, books, literature, writing"},
    "Finance & Crypto": {"kind": "utility", "color": "#ff8c00",
        "desc": "banking, stock market, investing, cryptocurrency, budgeting, taxes, loans, personal finance"},
    "Health & Fitness": {"kind": "utility", "color": "#ff6f61",
        "desc": "health conditions, symptoms, medicine, workouts, nutrition, diet, mental health, sleep"},
    "Shopping": {"kind": "utility", "color": "#ff69b4",
        "desc": "online shopping, product pages, reviews, deals, prices, shopping cart, orders"},
    "Travel & Maps": {"kind": "utility", "color": "#87ceeb",
        "desc": "flights, hotels, maps and directions, travel destinations, bookings, trips"},
    "Food & Cooking": {"kind": "utility", "color": "#deb887",
        "desc": "recipes, cooking, restaurants, food delivery, baking"},
    "Communication": {"kind": "utility", "color": "#b0c4de",
        "desc": "email inbox, chat, messaging, video calls"},
    "Home & Lifestyle": {"kind": "neutral", "color": "#d8bfd8",
        "desc": "home improvement, DIY, fashion, beauty, parenting, relationships, cars and vehicles, pets"},
    "Art & Design": {"kind": "neutral", "color": "#da70d6",
        "desc": "graphic design, illustration, photography, drawing, creative tools, UI design, architecture"},
    "Music & Audio": {"kind": "neutral", "color": "#ba55d3",
        "desc": "music, songs, albums, playlists, lyrics, podcasts, audio"},
    "Social Media": {"kind": "leisure", "color": "#ff3366",
        "desc": "social network feeds, posts, followers, reels, memes, threads, comments"},
    "Video & Streaming": {"kind": "leisure", "color": "#ff4500",
        "desc": "watching videos, streaming tv shows and movies, live streams, vlogs"},
    "Entertainment & Pop Culture": {"kind": "leisure", "color": "#ff7f50",
        "desc": "celebrities, movie and tv news, anime, comics, fandoms, trailers, gossip"},
    "Gaming": {"kind": "leisure", "color": "#9370db",
        "desc": "video games, gameplay, game guides, esports, game stores, walkthroughs"},
    "Sports": {"kind": "leisure", "color": "#1e90ff",
        "desc": "football, soccer, cricket, basketball, match scores, teams, athletes, highlights"},
    "Adult": {"kind": "leisure", "color": "#8b0000", "desc": "adult content"},
    "Search": {"kind": "utility", "color": "#c0c0c0", "desc": "search engine result pages"},
    "Other": {"kind": "neutral", "color": "#5f6f5f", "desc": "miscellaneous"},
}

LEISURE = {c for c, v in CATEGORIES.items() if v["kind"] == "leisure"}

# --- Domains ---------------------------------------------------------------

_D = {
    "Programming & Dev": """github.com gitlab.com bitbucket.org stackoverflow.com stackexchange.com
        superuser.com serverfault.com askubuntu.com npmjs.com pypi.org crates.io pkg.go.dev
        rubygems.org nuget.org packagist.org hub.docker.com docker.com kubernetes.io
        developer.mozilla.org docs.python.org python.org nodejs.org rust-lang.org go.dev golang.org
        typescriptlang.org reactjs.org react.dev vuejs.org angular.io svelte.dev nextjs.org
        tailwindcss.com getbootstrap.com w3schools.com geeksforgeeks.org leetcode.com hackerrank.com
        codeforces.com codechef.com replit.com codepen.io jsfiddle.net codesandbox.io vercel.com
        netlify.com heroku.com render.com fly.io aws.amazon.com console.aws.amazon.com cloud.google.com
        console.cloud.google.com portal.azure.com learn.microsoft.com dev.to hashnode.com
        jetbrains.com code.visualstudio.com huggingface.co kaggle.com regex101.com sqlite.org
        postgresql.org mysql.com mongodb.com redis.io digitalocean.com cloudflare.com
        dash.cloudflare.com readthedocs.io readthedocs.org godbolt.org exercism.org freecodecamp.org
        sourceforge.net jsdelivr.com unpkg.com caniuse.com mdn.io pytorch.org tensorflow.org
        anthropic.com/docs docs.anthropic.com platform.openai.com supabase.com firebase.google.com
        postman.com swagger.io graphql.org rust-lang.github.io crates.io""",
    "AI Tools": """chatgpt.com chat.openai.com openai.com claude.ai anthropic.com gemini.google.com
        bard.google.com perplexity.ai poe.com character.ai copilot.microsoft.com midjourney.com
        chat.mistral.ai mistral.ai deepseek.com chat.deepseek.com grok.com you.com phind.com
        runwayml.com elevenlabs.io suno.com suno.ai leonardo.ai ideogram.ai notebooklm.google.com
        aistudio.google.com lmarena.ai openrouter.ai""",
    "Work & Productivity": """docs.google.com drive.google.com sheets.google.com slides.google.com
        calendar.google.com keep.google.com notion.so notion.site trello.com asana.com monday.com
        clickup.com linear.app jira.com atlassian.net atlassian.com confluence.com airtable.com
        miro.com figma.com/file office.com office365.com sharepoint.com onedrive.live.com
        dropbox.com box.com evernote.com todoist.com basecamp.com zoho.com smartsheet.com
        coda.io obsidian.md canva.com/design salesforce.com hubspot.com docusign.com""",
    "Learning & Education": """coursera.org udemy.com edx.org khanacademy.org duolingo.com
        brilliant.org skillshare.com pluralsight.com udacity.com codecademy.com datacamp.com
        masterclass.com byjus.com unacademy.com chegg.com quizlet.com nptel.ac.in swayam.gov.in
        ocw.mit.edu classroom.google.com canvas.instructure.com instructure.com blackboard.com
        moodle.org studocu.com coursehero.com babbel.com memrise.com physicswallah.live""",
    "Research & Reference": """wikipedia.org wikimedia.org wiktionary.org britannica.com
        merriam-webster.com dictionary.com thesaurus.com scholar.google.com arxiv.org
        researchgate.net jstor.org semanticscholar.org pubmed.ncbi.nlm.nih.gov ncbi.nlm.nih.gov
        sciencedirect.com springer.com link.springer.com ieee.org ieeexplore.ieee.org acm.org
        dl.acm.org wolframalpha.com translate.google.com deepl.com archive.org stackprinter.com
        investopedia.com howstuffworks.com wikihow.com""",
    "Science": """nasa.gov space.com nature.com science.org scientificamerican.com newscientist.com
        phys.org sciencedaily.com quantamagazine.org livescience.com esa.int""",
    "Jobs & Career": """indeed.com glassdoor.com naukri.com monster.com wellfound.com angel.co
        ziprecruiter.com simplyhired.com levels.fyi internshala.com foundit.in hired.com
        workday.com myworkdayjobs.com greenhouse.io lever.co jobs.lever.co boards.greenhouse.io
        careers.google.com dice.com upwork.com fiverr.com freelancer.com""",
    "News & Politics": """news.google.com cnn.com bbc.com bbc.co.uk nytimes.com theguardian.com
        washingtonpost.com reuters.com apnews.com aljazeera.com foxnews.com nbcnews.com cbsnews.com
        abcnews.go.com npr.org bloomberg.com wsj.com ft.com economist.com politico.com thehill.com
        axios.com vox.com theatlantic.com newyorker.com time.com usatoday.com latimes.com
        independent.co.uk telegraph.co.uk dailymail.co.uk thehindu.com ndtv.com indiatimes.com
        timesofindia.indiatimes.com hindustantimes.com indianexpress.com news18.com
        livemint.com economictimes.indiatimes.com scroll.in thewire.in theprint.in dw.com
        france24.com cnbc.com msnbc.com newsweek.com huffpost.com buzzfeednews.com
        news.yahoo.com msn.com ground.news""",
    "Tech News & Gadgets": """theverge.com techcrunch.com arstechnica.com wired.com engadget.com
        gizmodo.com cnet.com zdnet.com tomshardware.com anandtech.com gsmarena.com 9to5mac.com
        9to5google.com macrumors.com androidauthority.com androidpolice.com xda-developers.com
        notebookcheck.net rtings.com digitaltrends.com techradar.com theregister.com
        slashdot.org thenextweb.com mashable.com""",
    "Reading & Blogs": """medium.com substack.com goodreads.com wattpad.com archiveofourown.org
        royalroad.com fanfiction.net longreads.com aeon.co lesswrong.com
        paulgraham.com gutenberg.org kindle.amazon.com read.amazon.com pocket.com getpocket.com
        blogspot.com wordpress.com tumblr.com/blog""",
    "Finance & Crypto": """paypal.com stripe.com chase.com bankofamerica.com wellsfargo.com
        citi.com capitalone.com americanexpress.com hdfcbank.com icicibank.com onlinesbi.sbi
        sbi.co.in axisbank.com kotak.com robinhood.com etrade.com fidelity.com schwab.com
        vanguard.com zerodha.com kite.zerodha.com groww.in upstox.com tradingview.com
        finance.yahoo.com marketwatch.com moneycontrol.com coinbase.com binance.com kraken.com
        coinmarketcap.com coingecko.com metamask.io wise.com revolut.com mint.intuit.com
        creditkarma.com nerdwallet.com turbotax.intuit.com paytm.com phonepe.com
        incometax.gov.in irs.gov""",
    "Health & Fitness": """webmd.com mayoclinic.org healthline.com medicalnewstoday.com nhs.uk
        clevelandclinic.org drugs.com medlineplus.gov cdc.gov who.int myfitnesspal.com strava.com
        fitbit.com garmin.com nike.com/training headspace.com calm.com betterhelp.com talkspace.com
        psychologytoday.com practo.com 1mg.com pharmeasy.in verywellhealth.com verywellmind.com
        examine.com""",
    "Shopping": """amazon.com amazon.in amazon.co.uk amazon.de amazon.ca ebay.com etsy.com
        walmart.com target.com bestbuy.com aliexpress.com alibaba.com temu.com shein.com
        flipkart.com myntra.com ajio.com meesho.com nykaa.com snapdeal.com ikea.com wayfair.com
        costco.com homedepot.com lowes.com newegg.com bhphotovideo.com zalando.com asos.com
        hm.com zara.com uniqlo.com nike.com adidas.com apple.com/shop store.steampowered.com/cart
        camelcamelcamel.com slickdeals.net pricehistory.app shopify.com""",
    "Travel & Maps": """maps.google.com google.com/maps booking.com airbnb.com expedia.com
        tripadvisor.com kayak.com skyscanner.com skyscanner.net makemytrip.com goibibo.com
        cleartrip.com agoda.com hotels.com trivago.com irctc.co.in uber.com lyft.com ola.com
        rapido.bike openstreetmap.org waze.com rome2rio.com lonelyplanet.com hostelworld.com
        united.com delta.com aa.com emirates.com ryanair.com easyjet.com indigo.in""",
    "Food & Cooking": """allrecipes.com foodnetwork.com seriouseats.com bonappetit.com epicurious.com
        tasty.co delish.com budgetbytes.com zomato.com swiggy.com ubereats.com doordash.com
        grubhub.com deliveroo.co.uk instacart.com blinkit.com zeptonow.com bigbasket.com
        yelp.com opentable.com nytimes.com/cooking cooking.nytimes.com""",
    "Communication": """mail.google.com gmail.com outlook.live.com outlook.office.com outlook.com
        mail.yahoo.com proton.me mail.proton.me web.whatsapp.com whatsapp.com messenger.com
        web.telegram.org telegram.org discord.com slack.com app.slack.com teams.microsoft.com
        teams.live.com zoom.us meet.google.com webex.com signal.org skype.com chat.google.com
        hey.com fastmail.com icloud.com/mail""",
    "Home & Lifestyle": """pinterest.com/pin houzz.com zillow.com redfin.com realtor.com rightmove.co.uk
        magicbricks.com 99acres.com nobroker.in apartments.com cars.com carwale.com cardekho.com
        autotrader.com edmunds.com kbb.com vogue.com gq.com elle.com sephora.com ulta.com
        babycenter.com whattoexpect.com thebump.com petfinder.com chewy.com tinder.com
        bumble.com hinge.co match.com okcupid.com""",
    "Art & Design": """dribbble.com behance.net figma.com deviantart.com artstation.com
        unsplash.com pexels.com pixabay.com flickr.com 500px.com canva.com adobe.com
        coolors.co fonts.google.com awwwards.com archdaily.com procreate.com""",
    "Music & Audio": """open.spotify.com spotify.com music.youtube.com soundcloud.com music.apple.com
        bandcamp.com genius.com last.fm tidal.com deezer.com pandora.com gaana.com jiosaavn.com
        wynk.in podcasts.apple.com podcasts.google.com pocketcasts.com overcast.fm audible.com
        audible.in shazam.com""",
    "Social Media": """facebook.com instagram.com x.com twitter.com tiktok.com reddit.com
        old.reddit.com threads.net threads.com bsky.app snapchat.com linkedin.com tumblr.com
        pinterest.com 9gag.com imgur.com quora.com vk.com weibo.com mastodon.social
        news.ycombinator.com lobste.rs ifunny.co knowyourmeme.com""",
    "Video & Streaming": """youtube.com youtu.be m.youtube.com netflix.com primevideo.com
        hotstar.com jiohotstar.com disneyplus.com hulu.com max.com hbomax.com peacocktv.com
        paramountplus.com tv.apple.com twitch.tv kick.com vimeo.com dailymotion.com
        crunchyroll.com funimation.com sonyliv.com zee5.com mxplayer.in rumble.com
        bilibili.com nebula.tv curiositystream.com plex.tv""",
    "Entertainment & Pop Culture": """imdb.com rottentomatoes.com letterboxd.com metacritic.com
        myanimelist.net anilist.org fandom.com tvtropes.org ew.com variety.com
        hollywoodreporter.com deadline.com screenrant.com cbr.com collider.com tmz.com
        people.com buzzfeed.com boredpanda.com webtoons.com mangadex.org filmibeat.com
        pinkvilla.com bollywoodhungama.com""",
    "Gaming": """store.steampowered.com steampowered.com steamcommunity.com epicgames.com
        gog.com itch.io ign.com gamespot.com polygon.com kotaku.com pcgamer.com eurogamer.net
        rockpapershotgun.com xbox.com playstation.com nintendo.com roblox.com minecraft.net
        chess.com lichess.org op.gg u.gg mobalytics.gg blitz.gg tracker.gg speedrun.com
        nexusmods.com curseforge.com howlongtobeat.com gamefaqs.gamespot.com game8.co
        poki.com crazygames.com""",
    "Sports": """espn.com espncricinfo.com cricbuzz.com bleacherreport.com skysports.com
        goal.com fotmob.com sofascore.com flashscore.com livescore.com nba.com nfl.com mlb.com
        nhl.com premierleague.com uefa.com fifa.com formula1.com si.com theathletic.com
        sportskeeda.com transfermarkt.com strava.com/segments""",
    "Adult": """pornhub.com xvideos.com xnxx.com xhamster.com onlyfans.com redtube.com
        youporn.com chaturbate.com stripchat.com spankbang.com rule34.xxx e-hentai.org nhentai.net""",
    "Search": """google.com bing.com duckduckgo.com search.yahoo.com yandex.com yandex.ru
        baidu.com ecosia.org search.brave.com startpage.com kagi.com""",
}

DOMAINS: dict[str, str] = {}
for _cat, _blob in _D.items():
    for _dom in _blob.split():
        DOMAINS.setdefault(_dom.lower(), _cat)

# Domains where the *title* decides (a programming tutorial on YouTube is learning).
SOFT_DOMAINS = {"youtube.com", "m.youtube.com", "youtu.be", "medium.com", "substack.com", "quora.com",
                "news.ycombinator.com", "vimeo.com", "twitch.tv", "bilibili.com", "rumble.com", "dailymotion.com"}
# Social sites: only a subreddit tells us more; post titles are too noisy ("This parking job" isn't job hunting)
SUBREDDIT_DOMAINS = {"reddit.com", "old.reddit.com"}

URL_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"linkedin\.com/(jobs|careers)"), "Jobs & Career"),
    (re.compile(r"google\.[a-z.]+/maps"), "Travel & Maps"),
    (re.compile(r"amazon\.[a-z.]+/(gp/video|primevideo)"), "Video & Streaming"),
    (re.compile(r"(google|bing|duckduckgo|yahoo|yandex|baidu|ecosia|startpage|kagi)\.[a-z.]+/(search|\?q=|html/?\?q=|s\?)"), "Search"),
    (re.compile(r"search\.brave\.com/search"), "Search"),
    (re.compile(r"youtube\.com/(feed/subscriptions|results)"), "Video & Streaming"),
    (re.compile(r"github\.com/[^/]+/[^/]+/(issues|pull)"), "Programming & Dev"),
]

# --- Feeds, search engines -------------------------------------------------

FEED_DOMAINS = {"reddit.com", "old.reddit.com", "x.com", "twitter.com", "instagram.com",
                "facebook.com", "tiktok.com", "threads.net", "threads.com", "bsky.app",
                "9gag.com", "tumblr.com", "pinterest.com", "news.ycombinator.com", "snapchat.com",
                "vk.com", "weibo.com", "imgur.com", "mastodon.social", "ifunny.co", "quora.com",
                "linkedin.com", "boredpanda.com"}
LONG_DWELL_URL = re.compile(
    r"(youtube\.com/watch|youtu\.be/|netflix\.com/watch|primevideo\.com|amazon\.[a-z.]+/gp/video|hotstar\.com|"
    r"disneyplus\.com|hulu\.com|max\.com|twitch\.tv/[^/]+$|coursera\.org/learn|udemy\.com/course|"
    r"crunchyroll\.com/watch|vimeo\.com/\d|nebula\.tv/videos|edx\.org/learn|khanacademy\.org/.+/v/)")
NOT_FEED_URL = re.compile(r"linkedin\.com/(jobs|learning|pulse)|reddit\.com/r/[^/]+/wiki")
FEED_URL = re.compile(r"(youtube\.com/shorts|linkedin\.com/feed|instagram\.com/reels?|facebook\.com/(reel|watch)|tiktok\.com/(foryou|@))")

SEARCH_PARAMS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(^|\.)google\.[a-z.]+$"), "q"),
    (re.compile(r"(^|\.)bing\.com$"), "q"),
    (re.compile(r"(^|\.)duckduckgo\.com$"), "q"),
    (re.compile(r"(^|\.)search\.yahoo\.com$"), "p"),
    (re.compile(r"(^|\.)yandex\.[a-z.]+$"), "text"),
    (re.compile(r"(^|\.)baidu\.com$"), "wd"),
    (re.compile(r"(^|\.)ecosia\.org$"), "q"),
    (re.compile(r"(^|\.)search\.brave\.com$"), "q"),
    (re.compile(r"(^|\.)startpage\.com$"), "query"),
    (re.compile(r"(^|\.)kagi\.com$"), "q"),
    (re.compile(r"(^|\.)perplexity\.ai$"), "q"),
    (re.compile(r"(^|\.)youtube\.com$"), "search_query"),
    (re.compile(r"(^|\.)amazon\.[a-z.]+$"), "k"),
    (re.compile(r"(^|\.)reddit\.com$"), "q"),
    (re.compile(r"(^|\.)github\.com$"), "q"),
    (re.compile(r"(^|\.)wikipedia\.org$"), "search"),
    (re.compile(r"(^|\.)flipkart\.com$"), "q"),
    (re.compile(r"(^|\.)ebay\.[a-z.]+$"), "_nkw"),
]

# --- Who owns what you visit (first-party visits only) ----------------------

PLATFORM_OWNERS: dict[str, list[str]] = {
    "Google": ["google", "youtube", "youtu.be", "gmail", "blogger", "android", "waze", "fitbit", "blogspot"],
    "Meta": ["facebook", "instagram", "whatsapp", "messenger", "threads", "oculus", "meta"],
    "Amazon": ["amazon", "twitch", "imdb", "audible", "goodreads", "primevideo", "zappos", "wholefoodsmarket"],
    "Microsoft": ["microsoft", "bing", "live", "outlook", "office", "office365", "linkedin", "github",
                  "xbox", "msn", "skype", "sharepoint", "onedrive"],
    "ByteDance": ["tiktok", "capcut", "lemon8-app"],
    "Apple": ["apple", "icloud"],
    "X Corp": ["x", "twitter"],
    "Reddit": ["reddit"],
    "OpenAI": ["openai", "chatgpt"],
    "Netflix": ["netflix"],
}

# --- What an ad-tech profile could infer ------------------------------------

SEGMENTS: list[dict] = [
    {"name": "Health condition research", "type": "sensitive",
     "kw": r"symptom|diagnos|treatment|disease|chronic|cancer|tumou?r|diabet|blood pressure|medication|side effects?|dosage|infection|rash|migraine|surgery",
     "domains": ["webmd.com", "mayoclinic.org", "healthline.com", "nhs.uk", "drugs.com", "medlineplus.gov", "medicalnewstoday.com", "clevelandclinic.org", "practo.com", "1mg.com"]},
    {"name": "Mental health", "type": "sensitive",
     "kw": r"anxiety|depress|therap(y|ist)|adhd|panic attack|burn ?out|ocd|bipolar|self[- ]?care|lonel|overthinking|mental health",
     "domains": ["betterhelp.com", "talkspace.com", "psychologytoday.com", "headspace.com", "calm.com", "verywellmind.com"]},
    {"name": "Sleep problems", "type": "sensitive",
     "kw": r"can'?t sleep|insomnia|melatonin|sleep schedule|always tired|tired all the time|fall asleep|sleep paralysis",
     "domains": []},
    {"name": "Financial stress", "type": "sensitive",
     "kw": r"\bloan|debt|credit score|payday|bankrupt|overdraft|\bemi\b|borrow money|collections agency|late payment|minimum payment",
     "domains": ["creditkarma.com", "nerdwallet.com"]},
    {"name": "Gambling & betting", "type": "sensitive",
     "kw": r"\bbet(ting)?\b|casino|poker|odds|lottery|jackpot|parlay|slots",
     "domains": ["bet365.com", "draftkings.com", "fanduel.com", "stake.com", "dream11.com", "pokerstars.com", "betway.com"]},
    {"name": "Job seeking", "type": "life_event",
     "kw": r"\bjobs?\b|hiring|resume|\bcv\b|interview|salary|layoff|recruiter|offer letter|notice period",
     "domains": ["indeed.com", "glassdoor.com", "naukri.com", "monster.com", "wellfound.com", "ziprecruiter.com", "levels.fyi", "linkedin.com/jobs"]},
    {"name": "Relationships & dating", "type": "sensitive",
     "kw": r"dating|tinder|bumble|hinge|break ?up|divorce|boyfriend|girlfriend|\bcrush\b|wedding|marriage|situationship",
     "domains": ["tinder.com", "bumble.com", "hinge.co", "match.com", "okcupid.com"]},
    {"name": "Pregnancy & parenting", "type": "life_event",
     "kw": r"pregnan|ovulation|newborn|toddler|\bivf\b|fertility|baby (names|food|sleep)|parenting",
     "domains": ["babycenter.com", "whattoexpect.com", "thebump.com"]},
    {"name": "Moving & housing", "type": "life_event",
     "kw": r"\brent\b|apartment|mortgage|real estate|movers|\blease\b|flatmate|roommate|house hunting",
     "domains": ["zillow.com", "redfin.com", "realtor.com", "rightmove.co.uk", "magicbricks.com", "99acres.com", "nobroker.in", "apartments.com"]},
    {"name": "Travel plans", "type": "commercial",
     "kw": r"flights?|hotel|\bvisa\b|itinerary|things to do in|best time to visit|resort",
     "domains": ["skyscanner.com", "skyscanner.net", "booking.com", "expedia.com", "airbnb.com", "makemytrip.com", "kayak.com", "agoda.com", "tripadvisor.com"]},
    {"name": "Big purchase research", "type": "commercial",
     "kw": r"\bbest .{2,30} (for|under|20\d\d)|\bvs\.?\b|review|price|buy|deal|discount|coupon|specs",
     "domains": ["rtings.com", "gsmarena.com", "camelcamelcamel.com", "slickdeals.net"]},
    {"name": "Politics & ideology", "type": "sensitive",
     "kw": r"election|\bvote\b|voting|democrat|republican|\bbjp\b|congress party|labour party|conservative|liberal|protest|left[- ]wing|right[- ]wing|manifesto",
     "domains": ["politico.com", "thehill.com"]},
    {"name": "Religion & beliefs", "type": "sensitive",
     "kw": r"church|mosque|temple|bible|quran|gita|prayer|\bgod\b|astrology|horoscope|zodiac|tarot",
     "domains": []},
    {"name": "Legal matters", "type": "sensitive",
     "kw": r"lawyer|attorney|lawsuit|arrest|\bcourt\b|\bbail\b|custody|\bfir\b|legal notice|sue\b",
     "domains": []},
]
for _seg in SEGMENTS:
    _seg["re"] = re.compile(_seg["kw"], re.I)

# --- Keyword rules for title-based categorization ----------------------------

KEYWORDS: dict[str, str] = {
    "Programming & Dev": r"python|javascript|typescript|java\b|c\+\+|golang|\brust\b|kotlin|swift|react|vue|angular|django|flask|fastapi|node\.?js|npm|pip install|docker|kubernetes|git\b|github|api\b|sql|database|regex|debug|stack ?trace|exception|error:|compile|algorithm|leetcode|coding|programming|developer|devops|linux|bash|terminal|frontend|backend|framework|library|refactor|unit test|pull request|deploy",
    "AI Tools": r"chatgpt|\bgpt-?\d|\bllm\b|claude|gemini|copilot|prompt|midjourney|stable diffusion|\bai\b agent|machine learning|neural network|deep learning|transformer model|fine-?tun|hugging ?face|ollama",
    "Work & Productivity": r"meeting|agenda|roadmap|quarterly|okr|spreadsheet|invoice|proposal|presentation|slides|standup|sprint|kanban|project plan|workspace|dashboard|untitled document|report",
    "Learning & Education": r"tutorial|course|lecture|lesson|learn|beginner|crash course|explained|how to|guide to|introduction to|study|exam|syllabus|homework|assignment|class \d|chapter|university|masterclass|full course",
    "Research & Reference": r"wikipedia|definition|meaning of|what is|history of|documentation|\bdocs\b|reference|paper|journal|arxiv|thesis|translate|encyclopedia",
    "Science": r"physics|quantum|biology|chemistry|astronomy|nasa|space ?x|telescope|galaxy|black hole|evolution|genetics|neuroscience|climate science|experiment",
    "Jobs & Career": r"\bjobs?\b|hiring|career|resume|interview|salary|internship|recruit|job description|apply now",
    "News & Politics": r"breaking|news|election|president|prime minister|minister|government|parliament|senate|policy|war\b|ukraine|gaza|israel|economy|inflation|supreme court|protest|politic|live updates",
    "Tech News & Gadgets": r"iphone|android|pixel \d|galaxy s\d|macbook|laptop|gpu|cpu|nvidia|amd|intel|unboxing|hands-on|smartphone|gadget|headphones|earbuds|smartwatch|tech news|launch event|keynote",
    "Reading & Blogs": r"essay|newsletter|blog|long read|book review|novel|chapter \d|poem|story|substack|medium",
    "Finance & Crypto": r"stock|shares|market|nifty|sensex|s&p|nasdaq|invest|portfolio|mutual fund|\betf\b|crypto|bitcoin|ethereum|trading|bank|loan|credit card|tax|budget|savings|ipo|dividend",
    "Health & Fitness": r"workout|exercise|gym|fitness|diet|calorie|protein|weight loss|yoga|running|symptom|health|doctor|medicine|sleep|mental health|anxiety|therapy|nutrition",
    "Shopping": r"buy|price|deal|sale|discount|coupon|cart|checkout|order|shipping|review:|best .{2,25} (under|for)|\bvs\b|product",
    "Travel & Maps": r"flight|hotel|trip|travel|itinerary|visa|airport|booking|map|directions|tour|vacation|beach|resort",
    "Food & Cooking": r"recipe|cook|bake|baking|restaurant|food|dinner|lunch|breakfast|dessert|chicken|pasta|biryani|curry|vegan|meal prep",
    "Communication": r"inbox|\bmail\b|gmail|outlook|message|chat|whatsapp|telegram|slack|discord|zoom|call\b",
    "Home & Lifestyle": r"diy|home decor|interior|furniture|fashion|outfit|skincare|makeup|haircut|parenting|relationship|dating|wedding|\bcar\b|bike|pets?\b|dog|cat\b|garden|cleaning",
    "Art & Design": r"design|illustration|drawing|sketch|painting|photography|photoshop|figma|blender|3d model|typography|logo|portfolio|art\b|animation",
    "Music & Audio": r"\bsong\b|music|album|lyrics|official audio|official video|playlist|\bmix\b|lofi|beats|concert|podcast|ft\.|feat\.|remix|cover\b|guitar|piano",
    "Social Media": r"on x:|tweet|thread|reddit|instagram|tiktok|facebook|reels?|memes?|followers|posts?\b|r/\w+",
    "Video & Streaming": r"episode|season \d|watch online|stream|vlog|full movie|trailer|reaction|shorts|live\b|netflix|prime video|hotstar",
    "Entertainment & Pop Culture": r"celebrity|actor|actress|movie|film|tv show|anime|manga|marvel|dc comics|star wars|box office|gossip|bollywood|hollywood|k-?pop|fandom|ending explained",
    "Gaming": r"gameplay|walkthrough|playthrough|\bgame\b|gaming|steam|xbox|playstation|ps5|nintendo|switch 2|minecraft|fortnite|valorant|league of legends|gta|elden ring|speedrun|esports|patch notes|build guide|tier list",
    "Sports": r"\bvs\.? .{2,20} (highlights|live|score)|cricket|football|soccer|\bnba\b|\bnfl\b|ipl|premier league|champions league|world cup|match|innings|goal|f1\b|formula 1|tennis|ufc|highlights|transfer news",
}
KEYWORD_RE = {cat: re.compile(rf"(?:{pat})", re.I) for cat, pat in KEYWORDS.items()}

SUBREDDIT_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"programming|python|javascript|webdev|learnprogramming|cpp|rust|golang|java|devops|sysadmin|linux|selfhosted|homelab|datascience|cscareerquestions|experienceddevs|reactjs|node|django|unity3d|gamedev"), "Programming & Dev"),
    (re.compile(r"chatgpt|openai|localllama|claudeai|singularity|artificial|machinelearning|stablediffusion"), "AI Tools"),
    (re.compile(r"worldnews|^news$|politics|geopolitics|europe|india|unitedkingdom|canada|australia|neutralpolitics"), "News & Politics"),
    (re.compile(r"gaming|games|pcgaming|ps5|xbox|nintendo|leagueoflegends|valorant|minecraft|fortnite|eldenring|steam|pcmasterrace|truegaming|genshin"), "Gaming"),
    (re.compile(r"movies|television|anime|manga|marvel|netflix|startrek|starwars|bollywood|kpop|popculturechat|harrypotter"), "Entertainment & Pop Culture"),
    (re.compile(r"personalfinance|investing|wallstreetbets|cryptocurrency|stocks|bitcoin|fire$|financialindependence|indianstreetbets|povertyfinance"), "Finance & Crypto"),
    (re.compile(r"fitness|loseit|running|bodyweightfitness|nutrition|health|mentalhealth|anxiety|adhd|depression|sleep|getdisciplined|decidingtobebetter"), "Health & Fitness"),
    (re.compile(r"science|space|physics|askscience|biology|chemistry|astronomy|everythingscience"), "Science"),
    (re.compile(r"books|writing|literature|suggestmeabook|fantasy|scifi"), "Reading & Blogs"),
    (re.compile(r"soccer|nba|nfl|cricket|formula1|sports|reddevils|gunners|chelseafc|ipl|tennis|mma"), "Sports"),
    (re.compile(r"buildapc|apple|android|hardware|technology|gadgets|iphone|mac$|pixel|samsung|headphones|mechanicalkeyboards"), "Tech News & Gadgets"),
    (re.compile(r"cooking|food|recipes|baking|mealprep|eatcheapandhealthy"), "Food & Cooking"),
    (re.compile(r"travel|solotravel|backpacking|digitalnomad"), "Travel & Maps"),
    (re.compile(r"jobs|careerguidance|recruitinghell|resumes|antiwork|workreform"), "Jobs & Career"),
    (re.compile(r"^art$|design|photography|graphic_design|drawing|illustration|blender"), "Art & Design"),
    (re.compile(r"music|listentothis|hiphopheads|indieheads|guitar|piano|wearethemusicmakers"), "Music & Audio"),
    (re.compile(r"explainlikeimfive|todayilearned|youshouldknow|lifeprotips|askhistorians|askscience"), "Research & Reference"),
]

# --- Emotional tone of what you read (lexicon) --------------------------------

EMOTIONS: dict[str, re.Pattern] = {
    "outrage": re.compile(r"outrage|slams?\b|blasts?\b|furious|destroys|owned|scandal|disgust|\bworst\b|\bhates?\b|fury|rage\b|betray|shameful|controvers|backlash|exposed|idiot|meltdown|humiliat|calls out|lashes out|fires back", re.I),
    "fear": re.compile(r"warning|crisis|collapse|danger|threat|deadly|panic|recession|layoffs?|emergency|scam|hacked|breach|\bwar\b|killed|outbreak|terrif|fear|alarming|doom|apocalyp|catastroph|shocking|urgent", re.I),
    "sadness": re.compile(r"\bdied\b|\bdeath\b|\bdies\b|grief|\bsad\b|lonely|depress|tragic|tragedy|loss of|mourn|heartbreak|\bcry\b|crying|funeral|passed away|goodbye", re.I),
    "joy": re.compile(r"funny|hilarious|wholesome|happy|amazing|\blove\b|cute|celebrat|\bwins?\b|beautiful|relaxing|lofi|satisfying|\bjoy\b|delight|heartwarming|best day|laugh", re.I),
    "curiosity": re.compile(r"\bhow\b|\bwhy\b|explained|guide|tutorial|learn|introduction|understanding|deep dive|history of|documentation|\bdocs\b|course|what is|science of", re.I),
    "excitement": re.compile(r"\bnew\b|launch|released?|announc|trailer|first look|leak|unboxing|reveal|giveaway|hype|finally|record[- ]breaking", re.I),
}
POSITIVE = {"joy", "curiosity", "excitement"}
NEGATIVE = {"outrage", "fear", "sadness"}

WORRY_SEARCH = re.compile(
    r"symptom|is it normal|should i be worried|how to stop|can'?t sleep|anxiety|panic|chest pain|"
    r"\blump\b|cancer|\bam i\b|why do i feel|why am i|depress|lonely|heart racing|dying|overthinking|"
    r"what'?s wrong with me|how long does .* last", re.I)

FACES = {
    "calm": "(￣ー￣)",
    "happy": "(＾▽＾)",
    "proud": "(•̀ᴗ•́)و",
    "curious": "(・o・)?",
    "worried": "(・_・;)",
    "alert": "(ʘ_ʘ)",
    "sleepy": "(－_－) zzZ",
    "sad": "(╥_╥)",
}


def kind_of(category: str) -> str:
    return CATEGORIES.get(category, CATEGORIES["Other"])["kind"]


def color_of(category: str) -> str:
    return CATEGORIES.get(category, CATEGORIES["Other"])["color"]
