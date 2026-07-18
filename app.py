import streamlit as st
import requests
import json
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime
import time

# --------------------------------------------------------------------------
# Configuration de la page Streamlit
# --------------------------------------------------------------------------
st.set_page_config(page_title="PMU Quinté+ Live", page_icon="🐎", layout="wide")

# Initialisation des variables de session
if "config" not in st.session_state:
    st.session_state.config = {"url": "", "method": "POST", "interval": 5}
if "course_data" not in st.session_state:
    st.session_state.course_data = None
if "partants_data" not in st.session_state:
    st.session_state.partants_data = []
if "odds_history" not in st.session_state:
    st.session_state.odds_history = pd.DataFrame(columns=["Heure", "Cheval", "Cote"])
if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = False

# --------------------------------------------------------------------------
# Fonctions de récupération des données
# --------------------------------------------------------------------------
def fetch_quinte_data(url, date_str, method):
    """Interroge le webhook n8n et met à jour l'historique des cotes"""
    try:
        if method == "GET":
            response = requests.get(url, params={"Date": date_str}, timeout=30)
        else:
            response = requests.post(url, json={"Date": date_str}, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        if isinstance(data, str):
            data = json.loads(data)
            
        st.session_state.course_data = data.get("Course")
        st.session_state.partants_data = data.get("Partants", [])
        
        # Enregistrement de l'historique des cotes
        heure_actuelle = datetime.now().strftime("%H:%M:%S")
        nouvelles_cotes = []
        
        for p in st.session_state.partants_data:
            cote = p.get("rapportDirect")
            if pd.notna(cote) and str(cote).replace('.','',1).isdigit():
                nouvelles_cotes.append({
                    "Heure": heure_actuelle,
                    "Cheval": f"N°{p.get('numPmu')}",
                    "Cote": float(cote)
                })
        
        if nouvelles_cotes:
            df_new = pd.DataFrame(nouvelles_cotes)
            st.session_state.odds_history = pd.concat([st.session_state.odds_history, df_new], ignore_index=True)
            
        st.toast(f"Données actualisées à {heure_actuelle} !", icon="✅")
        
    except Exception as e:
        st.error(f"Erreur de connexion au Webhook n8n : {e}")

def fetch_lonab_data():
    """Interroge le webhook LONAB"""
    lonab_url = "https://n8n-l0ej.onrender.com/webhook/be7db15c-cb97-4253-b59c-d7c362e07bc4"
    try:
        response = requests.get(lonab_url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        return {"error": str(e)}

# --------------------------------------------------------------------------
# Interface Utilisateur (Sidebar) : Configuration & LONAB
# --------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Configuration n8n")
    st.session_state.config["url"] = st.text_input(
        "URL du Webhook", 
        value=st.session_state.config["url"]
    )
    st.session_state.config["method"] = st.selectbox(
        "Méthode HTTP", 
        ["POST", "GET"], 
        index=["POST", "GET"].index(st.session_state.config["method"])
    )
    
    st.divider()
    
    st.header("🎰 Résultats LONAB")
    if st.button("Récupérer Résultats"):
        with st.spinner("Chargement..."):
            lonab_res = fetch_lonab_data()
            if "error" in lonab_res:
                st.error("Erreur serveur LONAB")
            else:
                st.write("**Arrivée :**", lonab_res.get("Arrivée:", "—"))
                st.write("**Ordre :**", lonab_res.get("Ordre", "—"))
                st.write("**Désordre :**", lonab_res.get("Désordre", "—"))
                
    st.divider()
    
    st.header("⏱️ Suivi Automatique")
    st.session_state.config["interval"] = st.slider(
        "Intervalle (min)", 
        min_value=1, max_value=30, value=st.session_state.config["interval"]
    )
    
    if st.toggle("Activer l'actualisation auto", value=st.session_state.auto_refresh):
        st.session_state.auto_refresh = True
        st.success(f"Suivi activé (toutes les {st.session_state.config['interval']} min)")
    else:
        st.session_state.auto_refresh = False
        
    if st.button("🗑️ Vider l'historique du graphique"):
        st.session_state.odds_history = pd.DataFrame(columns=["Heure", "Cheval", "Cote"])
        st.rerun()

# --------------------------------------------------------------------------
# Interface Principale
# --------------------------------------------------------------------------
st.title("🐎 PMU Quinté+ Client n8n")
st.write("Données de course en direct via ton workflow n8n.")

col1, col2 = st.columns([1, 3])
with col1:
    date_recherche = st.date_input("Date de la course")
with col2:
    st.write("")
    st.write("")
    if st.button("🔍 Lancer la requête manuelle", type="primary"):
        if not st.session_state.config["url"]:
            st.warning("Veuillez renseigner l'URL du webhook.")
        else:
            with st.spinner("Interrogation de n8n..."):
                fetch_quinte_data(
                    st.session_state.config["url"], 
                    date_recherche.strftime("%Y-%m-%d"), 
                    st.session_state.config["method"]
                )

# --------------------------------------------------------------------------
# Affichage des Résultats et du Graphique
# --------------------------------------------------------------------------
if st.session_state.course_data:
    course = st.session_state.course_data
    
    st.divider()
    st.subheader(f"🏁 {course.get('Reunion', '?')} {course.get('Course', '?')} — {course.get('Prix', '?')}")
    st.caption(f"{course.get('Discipline', '?')} • Allocation: {course.get('Allocation', '?')} € • Distance: {course.get('Distance', '?')}")
    
    if course.get("Arrivee"):
        st.success(f"🏆 Arrivée provisoire/définitive : {course.get('Arrivee')}")
    
    # Les 3 onglets originaux sont de retour
    tab1, tab2, tab3 = st.tabs(["📊 Tableau des Partants", "📋 Détails (Cartes)", "📈 Évolution des Cotes"])
    
    with tab1:
        if st.session_state.partants_data:
            df_partants = pd.DataFrame(st.session_state.partants_data)
            cols_to_show = {
                "numPmu": "N°", "nom": "Nom", "driver": "Driver", "entraineur": "Entraîneur",
                "musique": "Musique", "rapportDirect": "Cote", "favoris": "Favori"
            }
            df_filtered = df_partants[[c for c in cols_to_show.keys() if c in df_partants.columns]].rename(columns=cols_to_show)
            st.dataframe(df_filtered, use_container_width=True, hide_index=True)

    with tab2:
        st.write("Détails individuels des chevaux")
        cols = st.columns(3)
        for idx, partant in enumerate(st.session_state.partants_data):
            with cols[idx % 3]:
                with st.container(border=True):
                    is_fav = "⭐ " if partant.get("favoris") else ""
                    st.write(f"### {is_fav} N°{partant.get('numPmu')} - {partant.get('nom')}")
                    st.write(f"**Driver:** {partant.get('driver', '—')}")
                    st.write(f"**Entraîneur:** {partant.get('entraineur', '—')}")
                    st.write(f"**Musique:** {partant.get('musique', '—')}")
                    st.metric("Cote Actuelle", partant.get("rapportDirect", "—"))

    with tab3:
        st.write("Suivi en temps réel des rapports directs. L'application enregistre les cotes à chaque actualisation.")
        df_history = st.session_state.odds_history
        if not df_history.empty:
            fig, ax = plt.subplots(figsize=(10, 5))
            
            for cheval, data in df_history.groupby("Cheval"):
                ax.plot(data["Heure"], data["Cote"], marker='o', label=cheval)
            
            ax.set_ylabel("Cote Directe")
            ax.set_xlabel("Heure d'actualisation")
            ax.set_title("Évolution des cotes du Quinté+")
            ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize='small')
            plt.xticks(rotation=45)
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            
            st.pyplot(fig)
        else:
            st.info("Aucun historique pour le moment. L'historique commencera à se construire dès la prochaine actualisation.")

# --------------------------------------------------------------------------
# Logique de Rafraîchissement Automatique (Doit rester à la fin)
# --------------------------------------------------------------------------
if st.session_state.auto_refresh and st.session_state.config["url"]:
    time.sleep(st.session_state.config["interval"] * 60)
    
    fetch_quinte_data(
        st.session_state.config["url"], 
        date_recherche.strftime("%Y-%m-%d"), 
        st.session_state.config["method"]
    )
    
    st.rerun()
