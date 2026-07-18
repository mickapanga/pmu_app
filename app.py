import streamlit as st
import requests
import json
import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

# --------------------------------------------------------------------------
# Configuration de la page Streamlit
# --------------------------------------------------------------------------
st.set_page_config(page_title="PMU Quinté+ n8n", page_icon="🐎", layout="wide")

# Initialisation des variables de session (remplace les attributs de classe Tkinter)
if "config" not in st.session_state:
    st.session_state.config = {"url": "", "method": "POST"}
if "course_data" not in st.session_state:
    st.session_state.course_data = None
if "partants_data" not in st.session_state:
    st.session_state.partants_data = []

# --------------------------------------------------------------------------
# Fonctions de récupération des données
# --------------------------------------------------------------------------
def fetch_quinte_data(url, date_str, method):
    """Interroge le webhook n8n pour la course Quinté+"""
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
        st.success(f"{len(st.session_state.partants_data)} partants récupérés !")
        
    except Exception as e:
        st.error(f"Erreur de connexion au Webhook n8n : {e}")

def fetch_lonab_data():
    """Interroge le webhook LONAB"""
    # URL LONAB codée en dur depuis ton script d'origine
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
    st.header("⚙️ Configuration")
    st.session_state.config["url"] = st.text_input(
        "URL du Webhook n8n", 
        value=st.session_state.config["url"],
        placeholder="https://mon-n8n.exemple.com/webhook/..."
    )
    st.session_state.config["method"] = st.selectbox(
        "Méthode HTTP", 
        ["POST", "GET"], 
        index=["POST", "GET"].index(st.session_state.config["method"])
    )
    
    st.divider()
    
    st.header("🎰 LONAB")
    if st.button("Récupérer Résultats LONAB"):
        with st.spinner("Chargement LONAB..."):
            lonab_res = fetch_lonab_data()
            if "error" in lonab_res:
                st.error("Erreur serveur LONAB")
            else:
                st.write("**Arrivée :**", lonab_res.get("Arrivée:", "—"))
                st.write("**Ordre :**", lonab_res.get("Ordre", "—"))
                st.write("**Désordre :**", lonab_res.get("Désordre", "—"))

# --------------------------------------------------------------------------
# Interface Utilisateur (Principale) : Recherche
# --------------------------------------------------------------------------
st.title("🐎 PMU Quinté+ Client n8n")
st.write("Données de course en direct via ton workflow n8n.")

col1, col2 = st.columns([1, 3])
with col1:
    date_recherche = st.date_input("Date de la course")
with col2:
    st.write("") # Espacement
    st.write("")
    if st.button("🔍 Récupérer les données", type="primary"):
        if not st.session_state.config["url"]:
            st.warning("Veuillez renseigner l'URL du webhook dans la barre latérale.")
        else:
            with st.spinner("Interrogation de n8n..."):
                fetch_quinte_data(
                    st.session_state.config["url"], 
                    date_recherche.strftime("%Y-%m-%d"), 
                    st.session_state.config["method"]
                )

# --------------------------------------------------------------------------
# Affichage des Résultats
# --------------------------------------------------------------------------
if st.session_state.course_data:
    course = st.session_state.course_data
    
    st.divider()
    
    # En-tête de la course
    st.subheader(f"🏁 {course.get('Reunion', '?')} {course.get('Course', '?')} — {course.get('Prix', '?')}")
    st.caption(f"{course.get('Discipline', '?')} • Allocation: {course.get('Allocation', '?')} € • Distance: {course.get('Distance', '?')}")
    
    if course.get("Arrivee"):
        st.success(f"🏆 Arrivée : {course.get('Arrivee')}")
    
    # Création des onglets pour remplacer tes fenêtres de l'application de bureau
    tab1, tab2, tab3 = st.tabs(["📊 Tableau des Partants", "📋 Détails (Cartes)", "📈 Suivi Cotes"])
    
    with tab1:
        # Streamlit gère nativement les DataFrames Pandas pour de superbes tableaux
        if st.session_state.partants_data:
            df = pd.DataFrame(st.session_state.partants_data)
            # Sélection et renommage des colonnes pertinentes
            cols_to_show = {
                "numPmu": "N°", "nom": "Nom", "driver": "Driver", "entraineur": "Entraîneur",
                "musique": "Musique", "rapportDirect": "Cote Directe", "favoris": "Favori"
            }
            # Filtrer seulement les colonnes qui existent dans le JSON de retour
            df_filtered = df[[c for c in cols_to_show.keys() if c in df.columns]].rename(columns=cols_to_show)
            st.dataframe(df_filtered, use_container_width=True, hide_index=True)
            
    with tab2:
        st.write("Détails individuels des chevaux")
        # Affichage sous forme de grille responsive
        cols = st.columns(3)
        for idx, partant in enumerate(st.session_state.partants_data):
            with cols[idx % 3]:
                with st.container(border=True):
                    is_fav = "⭐ " if partant.get("favoris") else ""
                    st.write(f"### {is_fav} N°{partant.get('numPmu')} - {partant.get('nom')}")
                    st.write(f"**Driver:** {partant.get('driver', '—')}")
                    st.write(f"**Musique:** {partant.get('musique', '—')}")
                    st.metric("Cote Directe", partant.get("rapportDirect", "—"))

    with tab3:
        st.write("Évolution des cotes directes (nécessite de rafraîchir manuellement pour accumuler les données)")
        # Ici on utilise Matplotlib comme dans ton code d'origine, mais affiché via Streamlit
        if st.session_state.partants_data:
            fig, ax = plt.subplots(figsize=(10, 5))
            
            for partant in st.session_state.partants_data:
                # Ceci est une version statique simplifiée. Pour un vrai suivi dans le temps,
                # il faut stocker l'historique dans st.session_state.odds_history.
                num = partant.get("numPmu")
                cote = partant.get("rapportDirect")
                
                if pd.notna(cote):
                    # On trace un point. Dans un scénario de refresh, ce serait une ligne.
                    ax.scatter(str(num), cote, label=f"N°{num}")
            
            ax.set_ylabel("Cote directe")
            ax.set_xlabel("Numéro du cheval")
            ax.set_title("Cotes actuelles")
            ax.grid(True, alpha=0.3)
            
            st.pyplot(fig)
