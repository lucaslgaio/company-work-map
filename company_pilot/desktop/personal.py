"""Clearly personal surfaces stay on the Mac. Everything else on a work computer is work.

Local exclusions configured by the person are matched separately, exactly as stored.
This list is the built-in denylist: consumer apps and pages that are not company work.
A spreadsheet that merely mentions one of these names is only blocked when the window
title, the app id or the page address contains the name as a whole phrase or host.
WhatsApp, LinkedIn and YouTube are not listed: a construction firm uses them for work.
Workplace safety text is not listed: the word saúde alone is not a personal health app.
"""
import re
import unicodedata

# Hosts of consumer banking, brokerage, health, dating, streaming and personal social
# pages, plus password-manager sites. Match the host, not a substring of another name.
PERSONAL_HOSTS = (
    "nubank.com.br", "itau.com.br", "bradesco.com.br", "santander.com.br", "bb.com.br",
    "bancointer.com.br", "inter.co", "c6bank.com.br", "picpay.com", "xpi.com.br",
    "rico.com.vc", "clear.com.br", "binance.com", "mercadobitcoin.com.br", "avenue.us",
    "doctoralia.com.br", "drconsulta.com", "conexasaude.com.br",
    "tinder.com", "bumble.com", "happn.com", "badoo.com", "okcupid.com", "grindr.com",
    "netflix.com", "spotify.com", "disneyplus.com", "primevideo.com", "globoplay.globo.com",
    "play.max.com", "hbomax.com", "twitch.tv", "deezer.com", "music.apple.com",
    "instagram.com", "facebook.com", "fb.com", "tiktok.com", "twitter.com", "x.com",
    "snapchat.com", "threads.net",
    "1password.com", "bitwarden.com", "lastpass.com", "dashlane.com",
)

# Whole phrases in the app id or the window title. Accents are folded.
PERSONAL_PHRASES = (
    ("banking", ("nubank", "itau", "bradesco", "banco do brasil", "c6 bank", "picpay",
                 "xp investimentos", "mercado bitcoin", "binance", "home broker", "internet banking")),
    ("health", ("doctoralia", "dr consulta", "meu prontuario", "consulta medica", "exame de sangue", "apple health")),
    ("dating", ("tinder", "bumble", "happn", "badoo", "okcupid", "grindr")),
    ("streaming", ("netflix", "spotify", "disney plus", "globoplay", "prime video", "hbo max", "deezer", "twitch", "apple music")),
    ("social", ("instagram", "facebook", "tiktok", "snapchat", "threads", "twitter")),
    ("passwords", ("1password", "bitwarden", "lastpass", "dashlane", "keepass", "keychain access", "acesso as chaves")),
    ("private_window", ("incognito", "inprivate", "navegacao privada", "private browsing", "janela anonima", "aba anonima")),
    ("lock_screen", ("tela bloqueada", "tela de bloqueio", "lock screen", "iniciar sessao", "login window")),
)

LOCK_APPS = ("loginwindow", "screensaver", "screensavers")

_SECRET = re.compile(
    r"(?i)\b(senha|password|passwd|token|api[ _-]?key|secret|chave de api)\b\s*[:=]\s*\S+"
)


def normalized(value: str) -> str:
    folded = unicodedata.normalize("NFKD", value).casefold()
    letters = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in letters).split())


def _phrase(value: str, term: str) -> bool:
    needle = normalized(term)
    return bool(needle and " " + needle + " " in " " + normalized(value) + " ")


def _hosts(value: str) -> list[str]:
    found = []
    for match in re.finditer(r"https?://([^/\s?#]+)", value, re.I):
        host = match.group(1).lower().split("@")[-1].split(":")[0].rstrip(".")
        if host:
            found.append(host)
    return found


def _host_blocked(host: str) -> bool:
    return any(host == suffix or host.endswith("." + suffix) for suffix in PERSONAL_HOSTS)


def personal_reason(surface: dict) -> str | None:
    """Return a clearly_personal reason, or None when the surface can be shared."""
    if surface.get("incognito") is True or surface.get("private_window") is True:
        return "clearly_personal:private_window"
    app = str(surface.get("app_id", ""))
    title = str(surface.get("title", ""))
    ref = str(surface.get("record_ref", ""))
    if any(part in normalized(app) for part in LOCK_APPS):
        return "clearly_personal:lock_screen"
    for host in _hosts(ref) + _hosts(title):
        if _host_blocked(host):
            return "clearly_personal:address"
    blob = app + "\n" + title
    for category, phrases in PERSONAL_PHRASES:
        if any(_phrase(blob, phrase) for phrase in phrases):
            return "clearly_personal:" + category
    return None


def redact_secrets(text: str) -> str:
    """Replace a labeled secret value. Password fields omitted by the helper stay omitted."""
    return _SECRET.sub(lambda match: match.group(1) + ": [redigido]", text)
