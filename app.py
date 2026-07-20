"""
PMU Quinté+ — Application web (Streamlit)
===========================================

Version web de l'application de bureau « PMU Quinté+ — Client n8n »,
pour un accès depuis n'importe quelle plateforme (PC, Android, iOS...)
via un navigateur, en hébergeant ce script sur un serveur.

Toutes les fonctions de l'application de bureau sont conservées :
    - Profils de webhook n8n (plusieurs configurations sauvegardées)
    - Récupération de la course du jour (date + méthode GET/POST)
    - Compte à rebours jusqu'au départ + rafraîchissement automatique
      (cadence choisie avant le départ, puis 1 min automatiquement dès
      que le départ est passé, jusqu'à l'arrivée)
    - Cards détaillées par partant (favori en doré, non-partant grisé,
      libellé de statut masqué sauf non-partant)
    - Vue tableau complète de tous les partants
    - Suivi graphique des cotes directes dans le temps, avec filtre par
      clic sur la légende, boutons Tout afficher / Tout masquer / Arrivée,
      et export PDF (graphique + tableau récapitulatif)
    - Historique des cotes persisté sur disque (survit aux redémarrages)
    - Résultats LONAB (autre webhook n8n), dans leur propre onglet

Les couleurs, la palette et toutes les données/logiques métier sont
reprises à l'identique de l'application de bureau.

Dépendances :
    pip install streamlit requests plotly pandas matplotlib

Lancement en local :
    streamlit run pmu_quinte_streamlit.py

Déploiement sur un serveur (accessible de partout) :
    streamlit run pmu_quinte_streamlit.py --server.address 0.0.0.0 --server.port 8501
    (voir la note de déploiement en bas de ce fichier pour plus de détails,
    notamment l'exposition HTTPS via un reverse proxy)
"""

import json
import os
from datetime import datetime, timedelta
from io import BytesIO

import requests
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

import matplotlib
matplotlib.use("Agg")  # backend sans interface graphique, adapté à un serveur
from matplotlib.figure import Figure
from matplotlib.backends.backend_pdf import PdfPages

# --------------------------------------------------------------------------
# Chemins de persistance (identiques à l'application de bureau : profils
# webhook + historique des cotes survivent aux redémarrages du serveur)
# --------------------------------------------------------------------------
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".pmu_quinte_app_config.json")
ODDS_HISTORY_PATH = os.path.join(os.path.expanduser("~"), ".pmu_quinte_odds_history.json")
MAX_PERSISTED_RACES = 8  # nombre de courses gardées sur disque (les plus anciennes sont purgées)

# --------------------------------------------------------------------------
# Palette — thème "turf & soie de jockey" (vert course / or) — IDENTIQUE
# à l'application de bureau, ne pas modifier.
# --------------------------------------------------------------------------
BG_MAIN = "#121212"
BG_CARD = "#1b1b1b"
BG_CARD_ALT = "#212121"
BG_INPUT = "#262626"

BORDER = "#333333"
BORDER_SOFT = "#2a2a2a"

ACCENT = "#c9a227"          # or — actions principales, favoris
ACCENT_HOVER = "#a8871f"
ACCENT_TEXT_ON = "#1a1a1a"  # texte sombre sur fond or

GREEN = "#2e7d55"           # vert course — actions secondaires / succès
GREEN_HOVER = "#256744"
RED = "#d9534f"

TEXT_PRIMARY = "#f2f2f2"
TEXT_MUTED = "#9a9a9a"
TEXT_FAINT = "#6f6f6f"

FAVORI_ROW_BG = "#3a3220"
NON_PARTANT_ROW_BG = "#532a29"

# --------------------------------------------------------------------------
# Retry réseau — identique à l'application de bureau
# --------------------------------------------------------------------------
MAX_RETRIES = 3
RETRY_BACKOFF_BASE_S = 2  # délai = base ** tentative (2s, 4s, 8s)

# --------------------------------------------------------------------------
# LONAB — résultats des courses (autre webhook n8n)
# --------------------------------------------------------------------------
LONAB_WEBHOOK_URL = "https://n8n-l0ej.onrender.com/webhook/be7db15c-cb97-4253-b59c-d7c362e07bc4"
LONAB_REQUEST_TIMEOUT = 30

LONAB_INFO_FIELDS = [
    ("Course:", "🏁", "Course"),
    ("Arrivée:", "🏆", "Arrivée"),
    ("Non Partant:", "🚫", "Non partant(s)"),
]

LONAB_RAPPORT_FIELDS = [
    ("Ordre", "🎯", "Ordre"),
    ("Désordre", "🔀", "Désordre"),
    ("Bonus", "⭐", "Bonus"),
    ("C Gagnant", "🥇", "Couplé Gagnant"),
    ("C Placé A", "🥈", "Couplé Placé A"),
    ("C Placé B", "🥉", "Couplé Placé B"),
    ("C Placé C", "🏅", "Couplé Placé C"),
]

# --------------------------------------------------------------------------
# Rafraîchissement automatique — identique à l'application de bureau
# --------------------------------------------------------------------------
REFRESH_INTERVALS_S = {
    "1 min": 60,
    "2 min": 120,
    "5 min": 300,
    "10 min": 600,
}

DEFAULT_PROFILE_NAME = "Défaut"

# Palette de couleurs pour les courbes du graphique des cotes (tab20, en hex)
_TAB20_COLORS = [
    "#1f77b4", "#aec7e8", "#ff7f0e", "#ffbb78", "#2ca02c", "#98df8a",
    "#d62728", "#ff9896", "#9467bd", "#c5b0d5", "#8c564b", "#c49c94",
    "#e377c2", "#f7b6d2", "#7f7f7f", "#c7c7c7", "#bcbd22", "#dbdb8d",
    "#17becf", "#9edae5",
]


# ============================================================================
# Persistance (identique à l'application de bureau)
# ============================================================================
def load_config():
    default = {
        "profiles": {DEFAULT_PROFILE_NAME: {"url": "", "method": "POST"}},
        "active_profile": DEFAULT_PROFILE_NAME,
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if "profiles" not in cfg or not cfg["profiles"]:
                # Migration depuis l'ancien format (une seule URL, sans profils)
                cfg = {
                    "profiles": {
                        DEFAULT_PROFILE_NAME: {
                            "url": cfg.get("webhook_url", ""),
                            "method": cfg.get("method", "POST"),
                        }
                    },
                    "active_profile": DEFAULT_PROFILE_NAME,
                }
            if cfg.get("active_profile") not in cfg["profiles"]:
                cfg["active_profile"] = next(iter(cfg["profiles"]))
            return cfg
        except Exception:
            pass
    return default


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def load_odds_store():
    if os.path.exists(ODDS_HISTORY_PATH):
        try:
            with open(ODDS_HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def save_odds_store(store):
    try:
        with open(ODDS_HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def race_key_to_str(race_key):
    return "|".join(str(part) for part in race_key)


def parse_depart_utc(depart):
    """Convertit le bloc {Date, Heure} (GMT) de la course en datetime naïf UTC."""
    if not depart:
        return None
    try:
        date_str = depart.get("Date")
        heure_str = (depart.get("Heure") or "").replace("GMT", "").strip()
        if not date_str or not heure_str:
            return None
        return datetime.strptime(f"{date_str} {heure_str}", "%d-%m-%Y %H:%M")
    except (ValueError, TypeError):
        return None


def format_allocation(value):
    """Formate un montant en euros avec séparateur de milliers (espace)."""
    if value in (None, ""):
        return "?"
    try:
        n = float(value)
        text = f"{n:,.0f}".replace(",", " ")
        return f"{text} €"
    except (TypeError, ValueError):
        return str(value)


def extract_partant_fields(p):
    """Normalise un partant brut (JSON) en dict prêt à afficher — identique
    à l'application de bureau."""
    cote_d = p.get("rapportDirect", p.get("RapportDirect"))
    if isinstance(cote_d, dict):
        cote_d = cote_d.get("Valeur")

    cote_r = p.get("rapportReference", p.get("RapportReference"))
    if isinstance(cote_r, dict):
        cote_r = cote_r.get("Valeur")

    favori = p.get("favoris", p.get("Favoris"))
    favori_txt = "Oui" if favori else ("Non" if favori is not None else "")
    statut_txt = p.get("statut", p.get("Statut", "")) or ""

    return {
        "num": p.get("numPmu", p.get("Numero", "")),
        "nom": p.get("nom", p.get("Nom", "")) or "?",
        "age": p.get("age", p.get("Age", "")),
        "sexe": p.get("sexe", p.get("Sexe", "")),
        "statut": statut_txt.replace("_", " "),
        "allure": p.get("allure", p.get("Allure", "")) or "",
        "corde": p.get("placeCorde", p.get("PlaceCorde", "")),
        "oeilleres": p.get("oeilleres", p.get("Oeilleres", "")) or "",
        "deferre": p.get("deferre", p.get("Deferre", "")) or "",
        "handicap": p.get("handicapDistance", p.get("HandicapDistance", "")) or "",
        "driver": p.get("driver", p.get("Driver", "")) or "",
        "entraineur": p.get("entraineur", p.get("Entraineur", "")) or "",
        "musique": p.get("musique", p.get("Musique", "")) or "",
        "courses": p.get("nombreCourses", p.get("NombreCourses", "")),
        "victoires": p.get("nombreVictoires", p.get("NombreVictoires", "")),
        "places": p.get("nombrePlaces", p.get("NombrePlaces", "")),
        "places2": p.get("nombrePlacesSecond", p.get("NombrePlacesSecond", "")),
        "places3": p.get("nombrePlacesTroisieme", p.get("NombrePlacesTroisieme", "")),
        "taux": p.get("tauxVictoire", "") or "",
        "favori_txt": favori_txt,
        "is_favori": favori_txt == "Oui",
        "is_non_partant": "NON" in statut_txt.upper(),
        "cote_direct": cote_d,
        "cote_reference": cote_r,
    }


def _flatten_html(html):
    """Supprime toute indentation de chaque ligne d'un template HTML.

    Streamlit (Markdown/CommonMark) peut interpréter une ligne indentée de 4
    espaces ou plus comme un bloc de code et afficher le HTML en texte brut
    au lieu de le rendre. Comme les templates de ce fichier sont écrits avec
    l'indentation Python habituelle pour rester lisibles, on aplatit chaque
    ligne juste avant l'envoi à Streamlit pour éviter ce problème."""
    return "\n".join(line.strip() for line in html.strip().split("\n"))


def md_html(html):
    """st.markdown(..., unsafe_allow_html=True) avec le HTML aplati."""
    st.markdown(_flatten_html(html), unsafe_allow_html=True)


# ============================================================================
# CSS — reproduit exactement la palette et les composants de l'app de bureau
# ============================================================================
def inject_css():
    md_html(
        f"""
        <style>
        .stApp {{
            background-color: {BG_MAIN};
        }}
        /* Réduit le padding par défaut de Streamlit pour un rendu plus dense,
           façon application de bureau */
        .block-container {{
            padding-top: 1.2rem;
            padding-bottom: 2rem;
            max-width: 1300px;
        }}

        /* -- En-tête ------------------------------------------------------ */
        .pmu-header {{
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 6px;
        }}
        .pmu-badge {{
            width: 46px; height: 46px;
            border-radius: 10px;
            background: {ACCENT};
            display: flex; align-items: center; justify-content: center;
            font-size: 22px;
            flex-shrink: 0;
        }}
        .pmu-title {{
            font-size: 24px; font-weight: 800; color: {TEXT_PRIMARY};
            line-height: 1.2; margin: 0;
        }}
        .pmu-subtitle {{
            font-size: 12.5px; color: {TEXT_MUTED}; margin: 0;
        }}

        /* -- Cartes génériques --------------------------------------------*/
        .pmu-card {{
            background: {BG_CARD};
            border: 1px solid {BORDER};
            border-radius: 12px;
            padding: 16px;
            margin-bottom: 14px;
        }}
        .pmu-card-title {{
            font-size: 15px; font-weight: 800; color: {TEXT_PRIMARY};
            margin: 0 0 10px 0;
            display: flex; align-items: center; gap: 8px;
        }}
        .pmu-card-title .accent-bar {{
            width: 4px; height: 18px; border-radius: 2px; background: {ACCENT};
            display: inline-block;
        }}

        /* -- Lignes d'information (colonne de gauche) ----------------------*/
        .pmu-info-row {{
            display: flex; justify-content: space-between; align-items: center;
            background: {BG_CARD_ALT};
            border: 1px solid {BORDER_SOFT};
            border-radius: 8px;
            padding: 9px 12px;
            margin-bottom: 6px;
        }}
        .pmu-info-label {{
            color: {TEXT_FAINT}; font-size: 12px; font-weight: 700;
        }}
        .pmu-info-value {{
            color: {TEXT_PRIMARY}; font-size: 13.5px; font-weight: 700; text-align: right;
        }}
        .pmu-info-value.accent {{ color: {ACCENT}; font-size: 16px; }}
        .pmu-cond-box {{
            background: {BG_CARD_ALT};
            border: 1px solid {BORDER_SOFT};
            border-radius: 8px;
            padding: 10px 12px;
            margin-top: 4px;
        }}
        .pmu-cond-label {{
            color: {TEXT_FAINT}; font-size: 11px; font-weight: 700; margin-bottom: 4px;
        }}
        .pmu-cond-text {{ color: {TEXT_PRIMARY}; font-size: 13px; line-height: 1.4; }}

        .pmu-countdown {{
            color: {ACCENT}; font-weight: 800; font-size: 13.5px;
        }}

        /* -- Cards partants -------------------------------------------------*/
        .pmu-partant-card {{
            border-radius: 10px; border: 1px solid; padding: 12px 14px 10px 14px;
            margin-bottom: 14px;
        }}
        .pmu-p-header {{ display: flex; align-items: flex-start; gap: 10px; }}
        .pmu-p-badge {{
            width: 28px; height: 28px; min-width: 28px; border-radius: 14px;
            display: flex; align-items: center; justify-content: center;
            font-size: 12px; font-weight: 800;
        }}
        .pmu-p-name-col {{ flex: 1; min-width: 0; }}
        .pmu-p-name {{ font-size: 14px; font-weight: 800; word-break: break-word; }}
        .pmu-p-driver {{ font-size: 12px; color: {TEXT_MUTED}; margin-top: 1px; }}
        .pmu-p-cote-col {{ text-align: right; flex-shrink: 0; }}
        .pmu-p-cote-val {{ font-size: 16px; font-weight: 800; }}
        .pmu-p-cote-label {{ font-size: 10px; color: {TEXT_FAINT}; }}

        .pmu-p-tags {{ display: flex; align-items: center; gap: 8px; margin-top: 8px; flex-wrap: wrap; }}
        .pmu-pill {{
            display: inline-block; padding: 3px 9px; border-radius: 6px;
            font-size: 10.5px; font-weight: 800; background: {NON_PARTANT_ROW_BG};
            color: {TEXT_MUTED};
        }}
        .pmu-p-meta {{ font-size: 12px; color: {TEXT_MUTED}; }}
        .pmu-p-musique {{ font-size: 12px; color: {TEXT_MUTED}; margin-top: 6px; }}

        .pmu-stats-grid {{
            display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px;
            border-radius: 8px; padding: 8px; margin-top: 8px;
        }}
        .pmu-stat-label {{ font-size: 10.5px; font-weight: 800; color: {BG_MAIN}; }}
        .pmu-stat-value {{ font-size: 12.5px; font-weight: 800; }}

        .pmu-p-footer {{ font-size: 11px; color: {TEXT_MUTED}; margin-top: 8px; }}

        .pmu-legend {{ display: flex; gap: 16px; align-items: center; font-size: 12px; color: {TEXT_MUTED}; }}
        .pmu-legend-dot {{ width: 10px; height: 10px; border-radius: 4px; display: inline-block; margin-right: 5px; }}

        .pmu-status {{ color: {TEXT_MUTED}; font-size: 12.5px; }}
        .pmu-status.success {{ color: {GREEN}; }}
        .pmu-status.error {{ color: {RED}; }}

        /* -- Boutons Streamlit : accent or pour les boutons "primary" ------*/
        .stButton > button[kind="primary"] {{
            background-color: {ACCENT} !important;
            color: {ACCENT_TEXT_ON} !important;
            border: none !important;
            font-weight: 700 !important;
        }}
        .stButton > button[kind="primary"]:hover {{
            background-color: {ACCENT_HOVER} !important;
        }}

        /* Onglets */
        .stTabs [data-baseweb="tab-list"] {{ gap: 4px; }}
        .stTabs [data-baseweb="tab"] {{
            background-color: {BG_CARD_ALT}; border-radius: 8px 8px 0 0; color: {TEXT_MUTED};
        }}
        .stTabs [aria-selected="true"] {{
            background-color: {BG_CARD} !important; color: {ACCENT} !important;
        }}
        </style>
        """
    )


# ============================================================================
# État de session
# ============================================================================
def init_session_state():
    defaults = {
        "config_data": load_config(),
        "course_data": None,
        "partants_fields": [],
        "info_message": None,
        "status_text": "Prêt.",
        "status_kind": "info",
        "last_search": None,  # {"url", "date_str", "method"}
        "arrivee_known": False,
        "arrivee_nums": [],
        "race_start_utc": None,
        "race_start_notified": False,
        "auto_refresh_enabled": False,
        "refresh_interval_label": "2 min",
        "next_refresh_at": None,
        "current_race_key": None,
        "odds_history": {},
        "odds_names": {},
        "odds_favori": {},
        "odds_hidden_nums": set(),
        "odds_arrivee_filter_active": False,
        "persisted_odds_store": load_odds_store(),
        "lonab_data": None,
        "lonab_error": None,
        "lonab_last_updated": None,
        "lonab_loading": False,
        "date_value": datetime.now().date(),
        "method_value": "POST",
        "confirm_delete_profile": False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

    active_profile = st.session_state.config_data["profiles"].get(
        st.session_state.config_data["active_profile"], {}
    )
    if "url_value" not in st.session_state:
        st.session_state.url_value = active_profile.get("url", "")
    if "method_value" not in st.session_state or not st.session_state.get("_method_init"):
        st.session_state.method_value = active_profile.get("method", "POST")
        st.session_state._method_init = True


def set_status(text, kind="info"):
    st.session_state.status_text = text
    st.session_state.status_kind = kind


# ============================================================================
# Réseau — récupération course PMU (avec retry + repli progressif, identique
# à l'application de bureau)
# ============================================================================
def fetch_pmu_data(url, date_str, method):
    """Effectue la requête (avec tentatives) et renvoie (data, error_message)."""
    response = None
    last_exception = None
    attempts_made = 0

    for attempt in range(1, MAX_RETRIES + 1):
        attempts_made = attempt
        try:
            if method == "GET":
                response = requests.get(url, params={"Date": date_str}, timeout=30)
            else:
                response = requests.post(url, json={"Date": date_str}, timeout=30)
            response.raise_for_status()
            last_exception = None
            break
        except requests.exceptions.RequestException as exc:
            last_exception = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            is_client_error = status_code is not None and 400 <= status_code < 500
            is_last_attempt = attempt == MAX_RETRIES
            if is_client_error or is_last_attempt:
                break
            import time as _time
            _time.sleep(RETRY_BACKOFF_BASE_S ** attempt)

    if last_exception is not None:
        return None, f"Erreur réseau après {attempts_made} tentative(s) : {last_exception}"

    raw_text = response.text
    if not raw_text or not raw_text.strip():
        return None, (
            "Le webhook a répondu avec un corps VIDE.\n\n"
            "Dans n8n, sur le nœud 'Respond to Webhook' : mettez "
            "'Respond With' = JSON et 'Response Body' = {{ $json }}."
        )

    try:
        data = response.json()
        if isinstance(data, str):
            data = json.loads(data)
    except (ValueError, json.JSONDecodeError) as exc:
        preview = raw_text[:500]
        return None, f"Réponse illisible (pas du JSON valide) : {exc}\n\nContenu brut reçu :\n{preview}"

    return data, None


def fetch_lonab_data():
    last_exception = None
    attempts_made = 0
    response = None

    for attempt in range(1, MAX_RETRIES + 1):
        attempts_made = attempt
        try:
            response = requests.get(LONAB_WEBHOOK_URL, timeout=LONAB_REQUEST_TIMEOUT)
            response.raise_for_status()
            last_exception = None
            break
        except requests.exceptions.RequestException as exc:
            last_exception = exc
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            is_client_error = status_code is not None and 400 <= status_code < 500
            is_last_attempt = attempt == MAX_RETRIES
            if is_client_error or is_last_attempt:
                break
            import time as _time
            _time.sleep(RETRY_BACKOFF_BASE_S ** attempt)

    if last_exception is not None:
        if isinstance(last_exception, requests.exceptions.Timeout):
            return None, "Le serveur met trop de temps à répondre. Réessayez dans un instant."
        if isinstance(last_exception, requests.exceptions.ConnectionError):
            return None, "Connexion impossible. Vérifiez la connexion internet du serveur."
        if isinstance(last_exception, requests.exceptions.HTTPError):
            return None, f"Erreur du serveur ({last_exception})."
        return None, f"Erreur réseau après {attempts_made} tentative(s) : {last_exception}"

    try:
        data = response.json()
    except ValueError:
        return None, "Réponse du serveur illisible."

    return data, None


# ============================================================================
# Traitement des données reçues — équivalent de _display_data (bureau)
# ============================================================================
def load_persisted_odds_for_race(race_key):
    entry = st.session_state.persisted_odds_store.get(race_key_to_str(race_key))
    if not entry:
        return None

    history = {}
    for num_str, points in (entry.get("history") or {}).items():
        parsed = []
        for item in points:
            try:
                ts_str, value = item
                parsed.append((datetime.fromisoformat(ts_str), float(value)))
            except (ValueError, TypeError, IndexError):
                continue
        if parsed:
            try:
                history[int(num_str)] = parsed
            except (TypeError, ValueError):
                pass

    if not history:
        return None

    names = {}
    for num_str, nom in (entry.get("names") or {}).items():
        try:
            names[int(num_str)] = nom
        except (TypeError, ValueError):
            pass

    favori = {}
    for num_str, is_fav in (entry.get("favori") or {}).items():
        try:
            favori[int(num_str)] = bool(is_fav)
        except (TypeError, ValueError):
            pass

    return history, names, favori


def persist_odds_history():
    if not st.session_state.current_race_key or not st.session_state.odds_history:
        return

    key_str = race_key_to_str(st.session_state.current_race_key)
    st.session_state.persisted_odds_store[key_str] = {
        "names": {str(num): nom for num, nom in st.session_state.odds_names.items()},
        "favori": {str(num): bool(v) for num, v in st.session_state.odds_favori.items()},
        "history": {
            str(num): [[dt.isoformat(), value] for dt, value in points]
            for num, points in st.session_state.odds_history.items()
        },
        "saved_at": datetime.now().isoformat(),
    }

    store = st.session_state.persisted_odds_store
    if len(store) > MAX_PERSISTED_RACES:
        ordered = sorted(store.items(), key=lambda kv: kv[1].get("saved_at", ""))
        for old_key, _ in ordered[: len(ordered) - MAX_PERSISTED_RACES]:
            del store[old_key]

    save_odds_store(store)


def process_course_data(data, is_auto_refresh=False):
    """Met à jour tout l'état de session à partir d'une réponse JSON du
    webhook n8n. Équivalent exact de _display_data() dans l'app de bureau."""
    course = data.get("Course") if isinstance(data, dict) else None
    partants = data.get("Partants") if isinstance(data, dict) else None
    info_message = data.get("Message") if isinstance(data, dict) else None

    if course is None and partants is None and isinstance(data, dict):
        course = data if "Reunion" in data else None
        partants = data.get("Partants", []) if course else []

    st.session_state.info_message = info_message

    # Change de course : restaure un historique de cotes déjà persisté sur
    # disque pour CETTE course si disponible, sinon repart d'un historique vide.
    if course:
        depart_key = (course.get("Depart") or {}).get("Date")
        race_key = (course.get("Reunion"), course.get("Course"), depart_key)
        if race_key != st.session_state.current_race_key:
            st.session_state.current_race_key = race_key
            st.session_state.odds_hidden_nums = set()
            st.session_state.odds_arrivee_filter_active = False
            restored = load_persisted_odds_for_race(race_key)
            if restored:
                st.session_state.odds_history, st.session_state.odds_names, st.session_state.odds_favori = restored
            else:
                st.session_state.odds_history = {}
                st.session_state.odds_names = {}
                st.session_state.odds_favori = {}

    if course:
        depart = course.get("Depart", {}) or {}
        depart_utc = parse_depart_utc(depart)
        race_started = bool(depart_utc) and datetime.utcnow() >= depart_utc

        arrivee = course.get("Arrivee")
        if arrivee:
            arrivee_text = arrivee.replace(" - ", "  -  ")
        elif race_started:
            arrivee_text = "En attente de l'arrivée"
        else:
            arrivee_text = "Course non courue"

        st.session_state.arrivee_known = bool(arrivee)
        if arrivee:
            nums = []
            for bit in arrivee.split("-"):
                bit = bit.strip()
                if bit.isdigit():
                    nums.append(int(bit))
            st.session_state.arrivee_nums = nums
        else:
            st.session_state.arrivee_nums = []

        if depart_utc != st.session_state.race_start_utc:
            st.session_state.race_start_notified = False
        st.session_state.race_start_utc = depart_utc

        st.session_state.course_data = {
            "title": f"{course.get('Reunion', '?')} {course.get('Course', '?')} — {course.get('Prix', '')}",
            "subtitle": f"{course.get('Discipline', '?')} • {course.get('Specialité', '') or course.get('ConditionSexe', '') or 'Quinté+'}",
            "depart": f"{depart.get('Date', '?')} à {depart.get('Heure', '?')}",
            "distance": str(course.get("Distance", "?")),
            "discipline": str(course.get("Discipline", "?")),
            "corde": str(course.get("Corde", "?")),
            "allocation": format_allocation(course.get("Allocation")),
            "nb_partants": str(course.get("NombrePartants", "?")),
            "arrivee": arrivee_text,
            "conditions": str(course.get("Conditions", "") or "—"),
        }
    else:
        st.session_state.course_data = None
        st.session_state.race_start_utc = None
        st.session_state.arrivee_known = False
        st.session_state.arrivee_nums = []
        st.session_state.race_start_notified = False

    fields_list = []
    if partants:
        now = datetime.now()
        for p in partants:
            fields = extract_partant_fields(p)
            fields_list.append(fields)

            num = fields["num"]
            st.session_state.odds_names[num] = fields["nom"]
            st.session_state.odds_favori[num] = fields["is_favori"]
            try:
                cote_num = float(fields["cote_direct"])
            except (TypeError, ValueError):
                cote_num = None
            if cote_num is not None:
                st.session_state.odds_history.setdefault(num, []).append((now, cote_num))

        suffix = " (auto)" if is_auto_refresh else ""
        set_status(f"{len(partants)} partant(s) chargé(s){suffix}.", kind="success")
    else:
        if info_message:
            set_status(info_message, kind="info")
        else:
            set_status(
                "Aucun partant reçu. Vérifiez que le workflow n8n renvoie bien "
                "'Course' + 'Partants' en une seule réponse.",
                kind="error",
            )

    st.session_state.partants_fields = fields_list
    persist_odds_history()

    # Arrête l'auto-refresh dès que l'arrivée est connue ; sinon programme le
    # prochain rafraîchissement (1 min si le départ est passé, sinon l'intervalle choisi).
    if st.session_state.auto_refresh_enabled:
        if st.session_state.arrivee_known:
            st.session_state.auto_refresh_enabled = False
            st.session_state.next_refresh_at = None
            set_status("Arrivée affichée : rafraîchissement automatique arrêté.", kind="success")
        else:
            schedule_next_refresh()


def race_started():
    return bool(st.session_state.race_start_utc) and datetime.utcnow() >= st.session_state.race_start_utc


def schedule_next_refresh():
    if not st.session_state.auto_refresh_enabled or not st.session_state.last_search or st.session_state.arrivee_known:
        st.session_state.next_refresh_at = None
        return
    seconds = 60 if race_started() else REFRESH_INTERVALS_S.get(st.session_state.refresh_interval_label, 120)
    st.session_state.next_refresh_at = datetime.now() + timedelta(seconds=seconds)


def do_fetch(url, date_str, method, is_auto_refresh=False):
    data, error = fetch_pmu_data(url, date_str, method)
    if error is not None:
        if is_auto_refresh:
            set_status(f"Échec du rafraîchissement automatique : {error.splitlines()[0]}", kind="error")
        else:
            set_status("Erreur.", kind="error")
            st.session_state["_last_fetch_error"] = error
        return False
    process_course_data(data, is_auto_refresh=is_auto_refresh)
    return True


# ============================================================================
# Rendu — en-tête, config, recherche
# ============================================================================
def render_header():
    md_html(
        f"""
        <div class="pmu-header">
            <div class="pmu-badge">🐎</div>
            <div>
                <p class="pmu-title">PMU Quinté+</p>
                <p class="pmu-subtitle">Client n8n — données de course en direct (web)</p>
            </div>
        </div>
        """
    )


def render_config():
    cfg = st.session_state.config_data

    # Applique un changement de profil décidé lors du run précédent (création
    # ou suppression de profil), AVANT d'instancier le widget selectbox —
    # Streamlit interdit de modifier la clé d'un widget après son rendu dans
    # le même run, donc ce réglage doit se faire ici, en tout début de run.
    if "_pending_profile_switch" in st.session_state:
        st.session_state["profile_select"] = st.session_state["_pending_profile_switch"]
        del st.session_state["_pending_profile_switch"]

    has_url = bool(cfg["profiles"].get(cfg["active_profile"], {}).get("url"))

    with st.expander("⚙️ Configuration", expanded=not has_url):
        profile_names = list(cfg["profiles"].keys())
        col1, col2, col3 = st.columns([2, 1, 1])
        with col1:
            selectbox_kwargs = {"key": "profile_select"}
            if "profile_select" not in st.session_state:
                selectbox_kwargs["index"] = (
                    profile_names.index(cfg["active_profile"]) if cfg["active_profile"] in profile_names else 0
                )
            selected_profile = st.selectbox("Profil webhook", profile_names, **selectbox_kwargs)
        with col2:
            st.write("")
            st.write("")
            new_profile_clicked = st.button("➕ Nouveau", width="stretch")
        with col3:
            st.write("")
            st.write("")
            delete_clicked = st.button("🗑️ Supprimer", width="stretch")

        if selected_profile != cfg["active_profile"]:
            # Sauvegarde silencieuse du profil qu'on quitte
            cfg["profiles"][cfg["active_profile"]] = {
                "url": st.session_state.url_value, "method": st.session_state.method_value,
            }
            cfg["active_profile"] = selected_profile
            active = cfg["profiles"].get(selected_profile, {"url": "", "method": "POST"})
            st.session_state.url_value = active.get("url", "")
            st.session_state.method_value = active.get("method", "POST")
            save_config(cfg)
            st.rerun()

        if new_profile_clicked:
            st.session_state["_show_new_profile_form"] = True

        if st.session_state.get("_show_new_profile_form"):
            with st.form("new_profile_form", clear_on_submit=True):
                new_name = st.text_input("Nom du nouveau profil (ex : Test, Production)")
                submitted = st.form_submit_button("Créer")
                if submitted:
                    new_name = (new_name or "").strip()
                    if not new_name:
                        st.warning("Merci de saisir un nom.")
                    elif new_name in cfg["profiles"]:
                        st.error(f"Le profil « {new_name} » existe déjà.")
                    else:
                        cfg["profiles"][new_name] = {"url": "", "method": "POST"}
                        cfg["active_profile"] = new_name
                        save_config(cfg)
                        st.session_state.url_value = ""
                        st.session_state.method_value = "POST"
                        st.session_state["_pending_profile_switch"] = new_name
                        st.session_state["_show_new_profile_form"] = False
                        set_status(f"Profil « {new_name} » créé.", kind="success")
                        st.rerun()

        if delete_clicked:
            if len(cfg["profiles"]) <= 1:
                st.error("Impossible de supprimer le dernier profil restant.")
            else:
                st.session_state.confirm_delete_profile = True

        if st.session_state.confirm_delete_profile:
            st.warning(f"Supprimer le profil « {cfg['active_profile']} » ?")
            c1, c2 = st.columns(2)
            if c1.button("Oui, supprimer", type="primary", width="stretch"):
                name = cfg["active_profile"]
                del cfg["profiles"][name]
                new_active = next(iter(cfg["profiles"]))
                cfg["active_profile"] = new_active
                save_config(cfg)
                active = cfg["profiles"][new_active]
                st.session_state.url_value = active.get("url", "")
                st.session_state.method_value = active.get("method", "POST")
                st.session_state["_pending_profile_switch"] = new_active
                st.session_state.confirm_delete_profile = False
                set_status(f"Profil « {name} » supprimé.", kind="success")
                st.rerun()
            if c2.button("Annuler", width="stretch"):
                st.session_state.confirm_delete_profile = False
                st.rerun()

        st.text_input(
            "URL du webhook n8n", key="url_value",
            placeholder="https://mon-n8n.exemple.com/webhook/9b149691-...",
        )
        save_clicked = st.button("💾 Enregistrer", type="primary")
        if save_clicked:
            cfg["profiles"][cfg["active_profile"]] = {
                "url": st.session_state.url_value, "method": st.session_state.method_value,
            }
            save_config(cfg)
            set_status(f"Profil « {cfg['active_profile']} » enregistré.", kind="success")
            st.rerun()


def render_search_and_fetch():
    col1, col2, col3 = st.columns([2, 1, 2])
    with col1:
        st.date_input("Date de la course", key="date_value", format="YYYY-MM-DD")
    with col2:
        st.selectbox("Méthode", ["POST", "GET"], key="method_value")
    with col3:
        st.write("")
        fetch_clicked = st.button("🔍 Récupérer", type="primary", width="stretch")

    if fetch_clicked:
        cfg = st.session_state.config_data
        url = st.session_state.url_value.strip()
        if not url:
            st.error("Merci de renseigner l'URL du webhook n8n dans la Configuration.")
            return
        date_str = st.session_state.date_value.strftime("%Y-%m-%d")
        method = st.session_state.method_value

        cfg["profiles"][cfg["active_profile"]] = {"url": url, "method": method}
        save_config(cfg)

        st.session_state.last_search = {"url": url, "date_str": date_str, "method": method}
        with st.spinner("Récupération en cours..."):
            ok = do_fetch(url, date_str, method, is_auto_refresh=False)
        if not ok:
            st.error(st.session_state.get("_last_fetch_error", "Erreur inconnue."))
        st.rerun()


def render_status_bar():
    kind = st.session_state.status_kind
    dot_color = {"info": TEXT_FAINT, "success": GREEN, "error": RED}.get(kind, TEXT_FAINT)
    md_html(
        f"""<div class="pmu-status {kind}">
        <span style="color:{dot_color};">●</span>&nbsp; {st.session_state.status_text}
        </div>"""
    )


# ============================================================================
# Rendu — informations de la course (colonne de gauche)
# ============================================================================
def format_countdown(total_seconds_precise):
    total_seconds = int(total_seconds_precise)
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days > 0:
        return f"⏱ Départ dans {days}j {hours:02d}:{minutes:02d}:{seconds:02d}"
    if hours > 0:
        return f"⏱ Départ dans {hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"⏱ Départ dans {minutes:02d}:{seconds:02d}"


def render_auto_refresh_controls():
    col1, col2 = st.columns([1, 1])
    with col1:
        enabled = st.toggle("Auto", value=st.session_state.auto_refresh_enabled, key="auto_refresh_toggle_widget")
    with col2:
        interval_label = st.selectbox(
            "Intervalle", list(REFRESH_INTERVALS_S.keys()),
            index=list(REFRESH_INTERVALS_S.keys()).index(st.session_state.refresh_interval_label),
            key="refresh_interval_widget", label_visibility="collapsed",
        )

    if interval_label != st.session_state.refresh_interval_label:
        st.session_state.refresh_interval_label = interval_label
        if st.session_state.auto_refresh_enabled:
            schedule_next_refresh()

    if enabled != st.session_state.auto_refresh_enabled:
        if enabled:
            if not st.session_state.last_search:
                st.warning("Récupérez d'abord une course avant d'activer le rafraîchissement automatique.")
            elif st.session_state.arrivee_known:
                st.info("L'arrivée est déjà connue pour cette course, inutile de rafraîchir.")
            else:
                st.session_state.auto_refresh_enabled = True
                schedule_next_refresh()
                interval_txt = "1 min, course en cours" if race_started() else st.session_state.refresh_interval_label
                set_status(f"Rafraîchissement automatique activé ({interval_txt}).", kind="success")
                st.rerun()
        else:
            st.session_state.auto_refresh_enabled = False
            st.session_state.next_refresh_at = None
            set_status("Rafraîchissement automatique désactivé.", kind="info")
            st.rerun()


def _countdown_run_every():
    """Détermine si le fragment doit re-tick chaque seconde : uniquement
    quand un compte à rebours ou un rafraîchissement auto est réellement actif,
    pour ne pas solliciter le serveur inutilement le reste du temps."""
    if st.session_state.get("race_start_utc") and not st.session_state.get("arrivee_known"):
        return "1s"
    if st.session_state.get("auto_refresh_enabled"):
        return "1s"
    return None


@st.fragment(run_every=_countdown_run_every())
def render_countdown_fragment():
    race_start_utc = st.session_state.race_start_utc
    arrivee_known = st.session_state.arrivee_known

    if race_start_utc and not arrivee_known:
        delta = race_start_utc - datetime.utcnow()
        total_seconds_precise = delta.total_seconds()

        if total_seconds_precise <= 0:
            md_html('<div class="pmu-countdown">⏱ Départ dans 00:00:00</div>')
            # Bascule immédiate sur la cadence "1 minute" dès la fin de la
            # minuterie, au lieu d'attendre la fin de l'intervalle qui était
            # sélectionné avant le départ (une seule fois par course).
            if not st.session_state.race_start_notified:
                st.session_state.race_start_notified = True
                if st.session_state.auto_refresh_enabled:
                    schedule_next_refresh()
        else:
            md_html(f'<div class="pmu-countdown">{format_countdown(total_seconds_precise)}</div>')
    elif arrivee_known:
        md_html('<div class="pmu-countdown">🏁 Course terminée</div>')
    else:
        md_html('<div class="pmu-countdown">&nbsp;</div>')

    # Déclenche le rafraîchissement automatique si l'heure programmée est atteinte.
    if st.session_state.auto_refresh_enabled and st.session_state.last_search and not st.session_state.arrivee_known:
        next_at = st.session_state.next_refresh_at
        if next_at is None:
            schedule_next_refresh()
        elif datetime.now() >= next_at:
            search = st.session_state.last_search
            set_status("Rafraîchissement automatique en cours...", kind="info")
            do_fetch(search["url"], search["date_str"], search["method"], is_auto_refresh=True)
            schedule_next_refresh()
            # Un rerun complet (pas juste du fragment) est nécessaire pour que
            # les cards / le tableau / le graphique — rendus ailleurs sur la
            # page — reflètent les nouvelles données.
            st.rerun()


def render_course_info():
    course = st.session_state.course_data
    md_html('<div class="pmu-card">')

    if course:
        md_html(
            f"""
            <p class="pmu-card-title"><span class="accent-bar"></span>{course['title']}</p>
            <p class="pmu-subtitle" style="margin-bottom:8px;">{course['subtitle']}</p>
            """
        )
    else:
        subtitle = st.session_state.info_message or "Renseignez une date puis cliquez sur « Récupérer »."
        title = "Aucune course Quinté+ ce jour-là" if st.session_state.info_message else "Aucune course chargée"
        md_html(
            f"""
            <p class="pmu-card-title"><span class="accent-bar"></span>{title}</p>
            <p class="pmu-subtitle" style="margin-bottom:8px;">{subtitle}</p>
            """
        )

    # Compte à rebours (mis à jour chaque seconde) + réglages auto-refresh
    render_countdown_fragment()
    render_auto_refresh_controls()
    md_html("<div style='height:6px;'></div>")

    if course:
        rows = [
            ("🕒", "Départ", course["depart"], False),
            ("📏", "Distance", course["distance"], False),
            ("🏇", "Discipline", course["discipline"], False),
            ("↩", "Corde", course["corde"], False),
            ("💰", "Allocation", course["allocation"], False),
            ("👥", "Partants", course["nb_partants"], False),
            ("🏆", "Arrivée", course["arrivee"], True),
        ]
        rows_html = "".join(
            f"""<div class="pmu-info-row">
                    <span class="pmu-info-label">{icon} {label}</span>
                    <span class="pmu-info-value{' accent' if is_accent else ''}">{value}</span>
                </div>"""
            for icon, label, value, is_accent in rows
        )
        md_html(rows_html)

        md_html(
            f"""
            <div class="pmu-cond-box">
                <div class="pmu-cond-label">📋 CONDITIONS</div>
                <div class="pmu-cond-text">{course['conditions']}</div>
            </div>
            """
        )

    md_html("</div>")


# ============================================================================
# Rendu — cards des partants (identique visuellement à l'application de bureau)
# ============================================================================
def render_partant_card_html(fields):
    is_np = fields["is_non_partant"]
    is_fav = fields["is_favori"]

    if is_np:
        card_bg, border_color, name_color = BG_CARD_ALT, NON_PARTANT_ROW_BG, TEXT_FAINT
    elif is_fav:
        card_bg, border_color, name_color = FAVORI_ROW_BG, ACCENT, ACCENT
    else:
        # NB : reprend exactement le rendu (volontairement conservé) de
        # l'application de bureau, où les cards normales partagent le même
        # fond/bordure dorés que les favoris, seul le nom reste blanc.
        card_bg, border_color, name_color = FAVORI_ROW_BG, ACCENT, TEXT_PRIMARY

    def tc(normal_color):
        return TEXT_FAINT if is_np else normal_color

    badge_color = ACCENT if is_fav else (NON_PARTANT_ROW_BG if is_np else BG_MAIN)
    badge_text_color = ACCENT_TEXT_ON if is_fav else tc(TEXT_PRIMARY)

    name_text = f"⭐ {fields['nom']}" if is_fav else fields["nom"]
    driver_txt = " • ".join(t for t in [fields["driver"], fields["entraineur"]] if t) or "—"
    cote_val = fields["cote_direct"]
    cote_display = str(cote_val) if cote_val not in (None, "") else "—"

    pill_html = ""
    if is_np:
        pill_html = (
            f'<span class="pmu-pill" style="background:{NON_PARTANT_ROW_BG}; color:{TEXT_MUTED};">'
            f'{fields["statut"] or "NON PARTANT"}</span>'
        )

    meta_bits = []
    if fields["age"] not in (None, ""):
        meta_bits.append(f"{fields['age']} ans")
    if fields["sexe"]:
        meta_bits.append(str(fields["sexe"]))
    if fields["corde"] not in (None, ""):
        meta_bits.append(f"Corde {fields['corde']}")
    if fields["allure"]:
        meta_bits.append(str(fields["allure"]))
    meta_text = " • ".join(meta_bits)

    musique_html = ""
    if fields["musique"]:
        musique_html = (
            f'<div class="pmu-p-musique" style="color:{tc(TEXT_MUTED)};">'
            f'Musique : {fields["musique"]}</div>'
        )

    stats_bg = GREEN if is_fav else (NON_PARTANT_ROW_BG if is_np else GREEN)
    stat_defs = [
        ("Courses", fields["courses"]), ("Victoires", fields["victoires"]), ("Places", fields["places"]),
        ("Places 2e", fields["places2"]), ("Places 3e", fields["places3"]), ("Taux vict.", fields["taux"]),
    ]
    stats_html = "".join(
        f'<div><div class="pmu-stat-label">{label}</div>'
        f'<div class="pmu-stat-value" style="color:{tc(ACCENT)};">{value if value not in (None, "") else "—"}</div></div>'
        for label, value in stat_defs
    )

    footer_bits = []
    if fields["oeilleres"]:
        footer_bits.append(f"Œillères : {fields['oeilleres']}")
    if fields["deferre"]:
        footer_bits.append(f"Déferré : {fields['deferre']}")
    if fields["handicap"]:
        footer_bits.append(f"Handicap : {fields['handicap']}")
    footer_html = ""
    if footer_bits:
        footer_html = f'<div class="pmu-p-footer">{"  •  ".join(footer_bits)}</div>'

    return f"""
    <div class="pmu-partant-card" style="background:{card_bg}; border-color:{border_color};">
        <div class="pmu-p-header">
            <div class="pmu-p-badge" style="background:{badge_color}; color:{badge_text_color};">{fields['num']}</div>
            <div class="pmu-p-name-col">
                <div class="pmu-p-name" style="color:{name_color};">{name_text}</div>
                <div class="pmu-p-driver" style="color:{tc(TEXT_MUTED)};">{driver_txt}</div>
            </div>
            <div class="pmu-p-cote-col">
                <div class="pmu-p-cote-val" style="color:{tc(ACCENT)};">{cote_display}</div>
                <div class="pmu-p-cote-label">cote directe</div>
            </div>
        </div>
        <div class="pmu-p-tags">
            {pill_html}
            <span class="pmu-p-meta" style="color:{tc(TEXT_MUTED)};">{meta_text}</span>
        </div>
        {musique_html}
        <div class="pmu-stats-grid" style="background:{stats_bg};">{stats_html}</div>
        {footer_html}
    </div>
    """


def render_partants_tab():
    fields_list = st.session_state.partants_fields

    header_col1, header_col2 = st.columns([3, 2])
    with header_col1:
        md_html(
            f'<span style="color:{TEXT_PRIMARY}; font-weight:800; font-size:15px;">🏁 Partants</span> '
            f'<span style="color:{TEXT_MUTED};">({len(fields_list)})</span>'
        )
    with header_col2:
        md_html(
            f"""<div class="pmu-legend" style="justify-content:flex-end;">
                    <span><span class="pmu-legend-dot" style="background:{ACCENT};"></span>Favori</span>
                    <span><span class="pmu-legend-dot" style="background:{NON_PARTANT_ROW_BG};"></span>Non-partant</span>
                </div>"""
        )

    if not fields_list:
        md_html(
            f'<p style="color:{TEXT_MUTED}; text-align:center; padding:40px 0;">'
            f"Aucun partant chargé pour l'instant.<br>Récupérez une course pour afficher les cards.</p>"
        )
        return

    cols = st.columns(2)
    for idx, fields in enumerate(fields_list):
        with cols[idx % 2]:
            md_html(render_partant_card_html(fields))


# ============================================================================
# Rendu — onglet Tableau
# ============================================================================
TABLE_COLUMNS = [
    ("num", "N°"), ("nom", "Nom"), ("age", "Age"), ("sexe", "Sexe"), ("statut", "Statut"),
    ("allure", "Allure"), ("corde", "Corde"), ("oeilleres", "Œillères"), ("deferre", "Déferré"),
    ("handicap", "Handicap dist."), ("driver", "Driver/Jockey"), ("entraineur", "Entraîneur"),
    ("musique", "Musique"), ("courses", "Courses"), ("victoires", "Victoires"), ("places", "Places"),
    ("places2", "Places 2e"), ("places3", "Places 3e"), ("taux", "Taux victoire"),
    ("favori_txt", "Favori"), ("cote_direct", "Cote directe"), ("cote_reference", "Cote référence"),
]


def render_table_tab():
    fields_list = st.session_state.partants_fields
    if not fields_list:
        md_html(
            f'<p style="color:{TEXT_MUTED}; text-align:center; padding:40px 0;">'
            "Récupérez d'abord une course pour afficher le tableau des partants.</p>"
        )
        return

    keys = [k for k, _ in TABLE_COLUMNS]
    labels = [lbl for _, lbl in TABLE_COLUMNS]
    rows = [[("" if f.get(k) is None else str(f.get(k, ""))) for k in keys] for f in fields_list]
    df = pd.DataFrame(rows, columns=labels, dtype=str)

    def row_style(row):
        idx = row.name
        f = fields_list[idx]
        if f["is_non_partant"]:
            return [f"background-color: {NON_PARTANT_ROW_BG}; color: {TEXT_FAINT};"] * len(row)
        if f["is_favori"]:
            return [f"background-color: {FAVORI_ROW_BG}; color: {ACCENT};"] * len(row)
        bg = BG_CARD if idx % 2 else BG_CARD_ALT
        return [f"background-color: {bg}; color: {TEXT_PRIMARY};"] * len(row)

    styled = df.style.apply(row_style, axis=1)
    st.dataframe(styled, width="stretch", height=560, hide_index=True)


# ============================================================================
# Rendu — onglet Suivi des cotes (graphique Plotly interactif + export PDF)
# ============================================================================
def reset_odds_filter():
    st.session_state.odds_arrivee_filter_active = False
    st.session_state.odds_hidden_nums = set()


def hide_all_odds_filter():
    st.session_state.odds_arrivee_filter_active = False
    st.session_state.odds_hidden_nums = set(st.session_state.odds_history.keys())


def show_only_arrivee_odds_filter():
    if not st.session_state.arrivee_nums:
        st.session_state["_odds_arrivee_warning"] = True
        return
    arrivee_set = set(st.session_state.arrivee_nums)
    st.session_state.odds_arrivee_filter_active = True
    st.session_state.odds_hidden_nums = set(st.session_state.odds_history.keys()) - arrivee_set


def _sorted_odds_nums():
    return sorted(
        st.session_state.odds_history.keys(),
        key=lambda n: (not st.session_state.odds_favori.get(n, False), n),
    )


def build_odds_figure():
    fig = go.Figure()
    odds_history = st.session_state.odds_history
    odds_favori = st.session_state.odds_favori
    odds_names = st.session_state.odds_names
    hidden = st.session_state.odds_hidden_nums

    for i, num in enumerate(_sorted_odds_nums()):
        points = odds_history.get(num, [])
        if not points:
            continue
        times = [p[0] for p in points]
        values = [p[1] for p in points]
        is_fav = odds_favori.get(num, False)
        name = odds_names.get(num, str(num))
        label = f"{num}. {name}" + (" ★" if is_fav else "")
        color = ACCENT if is_fav else _TAB20_COLORS[i % len(_TAB20_COLORS)]
        fig.add_trace(go.Scatter(
            x=times, y=values, mode="lines+markers", name=label,
            line=dict(color=color, width=3.4 if is_fav else 2),
            marker=dict(size=6),
            visible=("legendonly" if num in hidden else True),
            hovertemplate="%{y:.1f}<extra>" + label + "</extra>",
        ))

    if st.session_state.odds_arrivee_filter_active and st.session_state.arrivee_nums:
        arrivee_str = " - ".join(str(n) for n in st.session_state.arrivee_nums)
        fig.add_annotation(
            text=f"Arrivée : {arrivee_str}", xref="paper", yref="paper",
            x=0.01, y=0.99, showarrow=False, font=dict(color=ACCENT, size=13),
            xanchor="left", yanchor="top",
        )

    fig.update_layout(
        paper_bgcolor=BG_CARD, plot_bgcolor=BG_CARD_ALT,
        font=dict(color=TEXT_MUTED, size=12),
        legend=dict(
            bgcolor=BG_CARD_ALT, bordercolor=BORDER, borderwidth=1,
            font=dict(color=TEXT_PRIMARY, size=11),
        ),
        margin=dict(l=55, r=20, t=20, b=40),
        xaxis=dict(gridcolor=BORDER_SOFT, tickfont=dict(color=TEXT_MUTED), tickformat="%H:%M:%S"),
        yaxis=dict(title="Cote directe", gridcolor=BORDER_SOFT, tickfont=dict(color=TEXT_MUTED)),
        height=480,
        hovermode="x unified",
    )
    if not odds_history:
        fig.add_annotation(
            text="Pas encore de données à afficher", xref="paper", yref="paper",
            x=0.5, y=0.5, showarrow=False, font=dict(color=TEXT_MUTED, size=14),
        )
    return fig


def build_odds_summary_rows():
    rows = []
    for num in _sorted_odds_nums():
        points = st.session_state.odds_history.get(num, [])
        if not points:
            continue
        name = st.session_state.odds_names.get(num, str(num))
        if st.session_state.odds_favori.get(num, False):
            name += " ★"
        first_val, last_val = points[0][1], points[-1][1]
        delta = last_val - first_val
        if delta > 0.001:
            trend = f"+{delta:.1f} ↗"
        elif delta < -0.001:
            trend = f"{delta:.1f} ↘"
        else:
            trend = "stable"
        rows.append([str(num), name, f"{first_val:.1f}", f"{last_val:.1f}", trend, str(len(points))])
    return rows


def current_race_display_text():
    course = st.session_state.course_data
    if course:
        return course["title"], course["subtitle"]
    return "Course", ""


def default_odds_pdf_filename():
    race_key = st.session_state.current_race_key
    if race_key:
        safe_bits = [
            str(b).strip().replace(" ", "_").replace("/", "-") for b in race_key if b
        ]
        if safe_bits:
            return "suivi_cotes_" + "_".join(safe_bits) + ".pdf"
    return f"suivi_cotes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"


def write_odds_pdf_bytes():
    """Construit le PDF (graphique + tableau récapitulatif) en mémoire, avec
    matplotlib — identique en contenu à l'application de bureau."""
    race_title, race_subtitle = current_race_display_text()
    exported_at = datetime.now().strftime("%d/%m/%Y à %H:%M:%S")
    buf = BytesIO()

    with PdfPages(buf) as pdf:
        # ---- Page 1 : graphique ----
        fig1 = Figure(figsize=(11.7, 8.3), dpi=150)
        fig1.patch.set_facecolor("white")
        gs = fig1.add_gridspec(nrows=10, ncols=1, hspace=0.6)

        ax_title = fig1.add_subplot(gs[0:1, 0])
        ax_title.axis("off")
        ax_title.text(0.0, 0.85, race_title, fontsize=16, weight="bold", color="#1a1a1a", ha="left", va="top")
        ax_title.text(0.0, 0.25, race_subtitle, fontsize=10, color="#555555", ha="left", va="top")
        ax_title.text(1.0, 0.85, f"Exporté le {exported_at}", fontsize=9, color="#777777", ha="right", va="top")

        ax = fig1.add_subplot(gs[1:, 0])
        sorted_nums = _sorted_odds_nums()
        hidden = st.session_state.odds_hidden_nums
        for i, num in enumerate(sorted_nums):
            if num in hidden:
                continue
            points = st.session_state.odds_history.get(num, [])
            if not points:
                continue
            times = [p[0] for p in points]
            values = [p[1] for p in points]
            is_fav = st.session_state.odds_favori.get(num, False)
            name = st.session_state.odds_names.get(num, str(num))
            label = f"{num}. {name}" + (" ★" if is_fav else "")
            color = ACCENT if is_fav else _TAB20_COLORS[i % len(_TAB20_COLORS)]
            ax.plot(times, values, marker="o", markersize=4, linewidth=2.4 if is_fav else 1.3, color=color, label=label)

        visible_count = len([n for n in sorted_nums if n not in hidden])
        if visible_count and visible_count <= 16:
            ax.legend(
                loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8,
                facecolor="white", edgecolor="#cccccc", labelcolor="#1a1a1a", borderaxespad=0.0,
            )
        if st.session_state.odds_arrivee_filter_active and st.session_state.arrivee_nums:
            arrivee_str = " - ".join(str(n) for n in st.session_state.arrivee_nums)
            ax.text(0.015, 0.97, f"Arrivée : {arrivee_str}", transform=ax.transAxes,
                    ha="left", va="top", fontsize=12, fontweight="bold", color=ACCENT)

        ax.set_ylabel("Cote directe", color="#555555", fontsize=10)
        ax.set_facecolor("white")
        for spine in ax.spines.values():
            spine.set_color("#cccccc")
        ax.tick_params(colors="#555555", labelsize=8)
        ax.grid(True, color="#e5e5e5", linewidth=0.5, alpha=0.6)
        import matplotlib.dates as mdates
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        for tick_label in ax.get_xticklabels():
            tick_label.set_rotation(30)
            tick_label.set_ha("right")
        ax.set_title("Évolution des cotes directes", fontsize=12, color="#1a1a1a", pad=10, loc="left")
        fig1.subplots_adjust(left=0.08, right=0.76, top=0.94, bottom=0.14)
        pdf.savefig(fig1)

        # ---- Page 2 : tableau récapitulatif ----
        rows = build_odds_summary_rows()
        fig2 = Figure(figsize=(11.7, 8.3), dpi=150)
        fig2.patch.set_facecolor("white")
        ax2 = fig2.add_subplot(111)
        ax2.axis("off")
        ax2.text(0.0, 0.98, "Récapitulatif des cotes suivies", fontsize=15, weight="bold",
                  color="#1a1a1a", ha="left", va="top", transform=ax2.transAxes)
        ax2.text(0.0, 0.93, race_title, fontsize=10, color="#555555", ha="left", va="top", transform=ax2.transAxes)

        if rows:
            col_labels = ["N°", "Cheval", "Cote départ", "Cote actuelle", "Variation", "Points"]
            table = ax2.table(cellText=rows, colLabels=col_labels, loc="upper left", cellLoc="left",
                               bbox=[0.0, 0.35, 1.0, 0.5])
            table.auto_set_font_size(False)
            table.set_fontsize(9)
            for (row_i, _col_i), cell in table.get_celld().items():
                cell.set_edgecolor("#dddddd")
                if row_i == 0:
                    cell.set_facecolor("#f0f0f0")
                    cell.set_text_props(weight="bold", color="#1a1a1a")
                else:
                    cell.set_facecolor("white")
                    cell.set_text_props(color="#1a1a1a")
        else:
            ax2.text(0.0, 0.8, "Aucune donnée à résumer.", fontsize=11, color="#777777",
                      ha="left", va="top", transform=ax2.transAxes)

        pdf.savefig(fig2)

        info = pdf.infodict()
        info["Title"] = f"Suivi des cotes directes — {race_title}"
        info["Author"] = "PMU Quinté+ — Client n8n (web)"
        info["Subject"] = "Historique des cotes directes"
        info["CreationDate"] = datetime.now()

    buf.seek(0)
    return buf.getvalue()


def render_odds_chart_tab():
    if not st.session_state.odds_history:
        md_html(
            f'<p style="color:{TEXT_MUTED}; text-align:center; padding:40px 0;">'
            "Aucun historique de cotes pour l'instant.<br>"
            "Récupérez une course, puis idéalement activez le rafraîchissement automatique "
            "pour observer l'évolution des cotes directes.</p>"
        )
        return

    st.caption("💡 Astuce : cliquez un cheval dans la légende du graphique pour l'afficher/le masquer.")

    bcol1, bcol2, bcol3, bcol4 = st.columns(4)
    if bcol1.button("↺ Tout afficher", width="stretch"):
        reset_odds_filter()
        st.rerun()
    if bcol2.button("🙈 Tout masquer", width="stretch"):
        hide_all_odds_filter()
        st.rerun()
    if bcol3.button("🏁 Arrivée", width="stretch"):
        show_only_arrivee_odds_filter()
        st.rerun()
    if st.session_state.pop("_odds_arrivee_warning", False):
        st.info("L'arrivée n'est pas encore connue pour cette course.")

    fig = build_odds_figure()
    st.plotly_chart(fig, width="stretch", theme=None)

    pdf_bytes = write_odds_pdf_bytes()
    bcol4.download_button(
        "📄 Export PDF", data=pdf_bytes, file_name=default_odds_pdf_filename(),
        mime="application/pdf", width="stretch", type="primary",
    )


# ============================================================================
# Rendu — onglet LONAB
# ============================================================================
def render_lonab_section_card(title, rows):
    rows_html = "".join(
        f"""<div class="pmu-info-row">
                <span class="pmu-info-label" style="color:{ACCENT};">{icon}</span>
                <span class="pmu-info-value" style="text-align:left; flex:1; margin-left:10px;">{value}</span>
            </div>"""
        for icon, value in rows
    )
    md_html(
        f"""<div class="pmu-card">
                <p class="pmu-card-title">{title}</p>
                {rows_html}
            </div>"""
    )


def render_lonab_tab():
    col1, col2 = st.columns([3, 1])
    with col1:
        st.caption("Résultats des courses hippiques (LONAB) — autre source n8n.")
    with col2:
        refresh_clicked = st.button("⟳ Actualiser", key="lonab_refresh", width="stretch", type="primary")

    if refresh_clicked or st.session_state.lonab_data is None and st.session_state.lonab_error is None:
        with st.spinner("Connexion au serveur en cours..."):
            data, error = fetch_lonab_data()
        st.session_state.lonab_data = data
        st.session_state.lonab_error = error
        st.session_state.lonab_last_updated = datetime.now()

    if st.session_state.lonab_error:
        md_html('<p style="text-align:center; font-size:28px;">⚠️</p>')
        md_html(f'<p style="color:{TEXT_MUTED}; text-align:center;">{st.session_state.lonab_error}</p>')
        st.caption(f"Échec — {datetime.now().strftime('%H:%M:%S')}")
        return

    data = st.session_state.lonab_data
    has_content = isinstance(data, dict) and any(data.get(key) for key, _icon, _label in LONAB_INFO_FIELDS)

    if not has_content:
        md_html(
            f'<p style="color:{TEXT_MUTED}; text-align:center; padding:40px 0;">'
            "Aucun résultat disponible pour le moment.<br>Réessayez plus tard.</p>"
        )
    else:
        render_lonab_section_card(
            "📋 Informations de la course",
            [(icon, data.get(key) or "—") for key, icon, _label in LONAB_INFO_FIELDS],
        )
        render_lonab_section_card(
            "💰 Rapports",
            [(icon, data.get(key) or "—") for key, icon, _label in LONAB_RAPPORT_FIELDS],
        )

    if st.session_state.lonab_last_updated:
        st.caption(f"Dernière mise à jour : {st.session_state.lonab_last_updated.strftime('%d/%m/%Y à %H:%M:%S')}")
    st.link_button("Voir lonab.bf", "https://lonab.bf")


# ============================================================================
# Assemblage principal
# ============================================================================
def main():
    st.set_page_config(
        page_title="PMU Quinté+", page_icon="🐎", layout="wide",
        initial_sidebar_state="collapsed",
    )
    init_session_state()
    inject_css()

    render_header()
    render_config()
    render_search_and_fetch()
    render_status_bar()

    md_html("<div style='height:10px;'></div>")

    col_info, col_main = st.columns([2, 3])
    with col_info:
        render_course_info()
    with col_main:
        tab_partants, tab_table, tab_odds, tab_lonab = st.tabs(
            ["🏇 Partants", "📊 Tableau", "📈 Suivi cotes", "🎰 LONAB"]
        )
        with tab_partants:
            render_partants_tab()
        with tab_table:
            render_table_tab()
        with tab_odds:
            render_odds_chart_tab()
        with tab_lonab:
            render_lonab_tab()


if __name__ == "__main__":
    main()


# ============================================================================
# Note de déploiement
# ============================================================================
# Pour un accès depuis n'importe où (PC, Android, iPhone...) :
#
# 1) Sur un serveur (VPS, Render, Railway, PythonAnywhere, etc.) :
#      pip install streamlit requests plotly pandas matplotlib
#      streamlit run pmu_quinte_streamlit.py --server.address 0.0.0.0 --server.port 8501
#
# 2) Exposez le port 8501 publiquement (idéalement derrière un reverse proxy
#    HTTPS comme Caddy ou Nginx + Let's Encrypt, pour un accès sécurisé et
#    une belle URL type https://pmu.mondomaine.com).
#
# 3) Ouvrez cette URL depuis n'importe quel navigateur (PC ou mobile) : le
#    site s'adapte automatiquement à la taille de l'écran (les colonnes se
#    réorganisent verticalement sur téléphone).
#
# 4) Les profils webhook et l'historique des cotes sont stockés côté serveur
#    (fichiers JSON dans le dossier utilisateur du serveur) : toutes les
#    plateformes qui se connectent à cette même URL partagent les mêmes
#    données, exactement comme avec l'application de bureau.
