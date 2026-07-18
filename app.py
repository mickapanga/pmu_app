import streamlit as st
import requests
import pandas as pd
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# Configuration de la page
st.set_page_config(page_title="PMU Quinté+ — Client n8n", layout="wide", initial_sidebar_state="expanded")

LONAB_WEBHOOK_URL = "https://n8n-l0ej.onrender.com/webhook/be7db15c-cb97-4253-b59c-d7c362e07bc4"

# Initialisation de l'historique des cotes en session
if "odds_history" not in st.session_state:
    st.session_state.odds_history = {}

# --- Barre latérale : Configuration ---
st.sidebar.title("⚙️ Configuration")
webhook_url = st.sidebar.text_input("URL du Webhook n8n", placeholder="https://...")
method = st.sidebar.selectbox("Méthode HTTP", ["POST", "GET"])
date_course = st.sidebar.date_input("Date de la course", datetime.now())

# Bouton de récupération principal
fetch_btn = st.sidebar.button("🔍 Récupérer la course", use_container_width=True)

# --- Fonction de récupération ---
def fetch_data(url, date_str, method):
    try:
        if method == "GET":
            res = requests.get(url, params={"Date": date_str}, timeout=15)
        else:
            res = requests.post(url, json={"Date": date_str}, timeout=15)
        res.raise_for_status()
        return res.json()
    except Exception as e:
        st.error(f"Erreur de connexion : {e}")
        return None

# --- Affichage Principal ---
st.title("🐎 PMU Quinté+ — Client n8n")

if fetch_btn and webhook_url:
    with st.spinner("Récupération des données..."):
        date_str = date_course.strftime("%Y-%m-%d")
        data = fetch_data(webhook_url, date_str, method)
        
        if data and "Course" in data:
            course = data["Course"]
            partants = data.get("Partants", [])
            
            # Mise à jour historique des cotes
            now = datetime.now()
            for p in partants:
                num = p.get("numPmu", p.get("Numero"))
                nom = p.get("nom", p.get("Nom"))
                cote = p.get("rapportDirect", p.get("RapportDirect"))
                if cote:
                    if num not in st.session_state.odds_history:
                        st.session_state.odds_history[num] = {"nom": nom, "points": []}
                    st.session_state.odds_history[num]["points"].append((now, float(cote)))

            # Layout 2 colonnes : Infos Course / Partants
            col1, col2 = st.columns([1, 2])
            
            with col1:
                st.header("📋 Infos Course")
                st.subheader(f"{course.get('Reunion')} {course.get('Course')} - {course.get('Prix')}")
                st.write(f"**Discipline :** {course.get('Discipline')}")
                st.write(f"**Distance :** {course.get('Distance')}")
                st.write(f"**Allocation :** {course.get('Allocation')} €")
                st.write(f"**Arrivée :** :gold[{course.get('Arrivee', 'Non connue')}]")
                st.info(f"**Conditions :** {course.get('Conditions')}")
                
            with col2:
                st.header(f"🏁 Partants ({len(partants)})")
                
                # Onglets pour basculer la vue
                tab_cards, tab_table, tab_chart = st.tabs(["🗂️ Cartes", "📊 Tableau", "📈 Graphique Cotes"])
                
                with tab_cards:
                    for p in partants:
                        with st.container(border=True):
                            c_num, c_nom, c_cote = p.get("numPmu"), p.get("nom"), p.get("rapportDirect", "—")
                            st.write(f"**N°{c_num} - {c_nom}** | Cote en direct : :green[{c_cote}]")
                            st.write(f"Driver: {p.get('driver')} | Musique: {p.get('musique')}")
                
                with tab_table:
                    df = pd.DataFrame(partants)
                    st.dataframe(df)
                    
                with tab_chart:
                    if st.session_state.odds_history:
                        fig, ax = plt.subplots()
                        for num, h_data in st.session_state.odds_history.items():
                            if h_data["points"]:
                                times = [pt[0] for pt in h_data["points"]]
                                values = [pt[1] for pt in h_data["points"]]
                                ax.plot(times, values, marker="o", label=f"{num}. {h_data['nom']}")
                        ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
                        plt.xticks(rotation=45)
                        ax.legend()
                        st.pyplot(fig)
        else:
            st.warning("Aucune donnée renvoyée par le serveur.")