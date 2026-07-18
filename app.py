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
# NOUVEAU : Historique pour le graphique
if "odds_history" not in st.session_state:
    st.session_state.odds_history = pd.DataFrame(columns=["Heure", "Cheval", "Cote"])
# NOUVEAU : État de l'auto-refresh
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
            # S'assurer que la cote est un nombre valide avant de l'enregistrer
            if cote and str(cote).replace('.','',1).isdigit():
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

# --------------------------------------------------------------------------
# Interface Utilisateur (Sidebar) : Configuration
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
    
    st.header("⏱️ Suivi Automatique")
    st.session_state.config["interval"] = st.slider(
        "Intervalle d'actualisation (min)", 
        min_value=1, max_value=30, value=st.session_state.config["interval"]
    )
    
    # Bouton bascule pour l'auto-refresh
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
st.title("🐎 PMU Live Tracker")

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
    
    tab1, tab2 = st.tabs(["📈 Évolution des Cotes", "📊 Partants en Direct"])
    
    with tab1:
        st.write("Suivi en temps réel des rapports directs. Laissez l'application ouverte avec le *Suivi Automatique* activé pour voir les courbes se dessiner.")
        
        df_history = st.session_state.odds_history
        if not df_history.empty:
            # Création du graphique d'évolution
            fig, ax = plt.subplots(figsize=(10, 5))
            
            # Grouper par cheval et tracer une ligne pour chacun
            for cheval, data in df_history.groupby("Cheval"):
                ax.plot(data["Heure"], data["Cote"], marker='o', label=cheval)
            
            ax.set_ylabel("Cote Directe")
            ax.set_xlabel("Heure d'actualisation")
            ax.set_title("Évolution des cotes du Quinté+")
            
            # Gérer la lisibilité de la légende s'il y a 16 partants
            ax.legend(bbox_to_anchor=(1.05, 1), loc='upper left', fontsize='small')
            plt.xticks(rotation=45)
            plt.tight_layout()
            
            st.pyplot(fig)
        else:
            st.info("Aucun historique pour le moment. Lancez une requête pour commencer.")

    with tab2:
        if st.session_state.partants_data:
            df_partants = pd.DataFrame(st.session_state.partants_data)
            cols_to_show = {
                "numPmu": "N°", "nom": "Nom", "driver": "Driver", "entraineur": "Entraîneur",
                "rapportDirect": "Cote Actuelle"
            }
            df_filtered = df_partants[[c for c in cols_to_show.keys() if c in df_partants.columns]].rename(columns=cols_to_show)
            st.dataframe(df_filtered, use_container_width=True, hide_index=True)

# --------------------------------------------------------------------------
# Logique de Rafraîchissement Automatique (Doit rester à la toute fin du code)
# --------------------------------------------------------------------------
if st.session_state.auto_refresh and st.session_state.config["url"]:
    # Met le script en pause selon l'intervalle choisi
    time.sleep(st.session_state.config["interval"] * 60)
    
    # Exécute la requête pour mettre à jour les données
    fetch_quinte_data(
        st.session_state.config["url"], 
        date_recherche.strftime("%Y-%m-%d"), 
        st.session_state.config["method"]
    )
    
    # Relance l'interface entière pour afficher les nouveautés
    st.rerun()
