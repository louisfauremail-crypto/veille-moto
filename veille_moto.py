#!/usr/bin/env python3
"""
Veille des créneaux PLATEAU Marietton -> alerte e-mail.

Les identifiants sont dans le fichier .env (même dossier que ce script).
MODE (ci-dessous) : "test-mail", "debug", "unique" ou "boucle".
"""
import datetime as dt
import json
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import sync_playwright

# ============ RÉGLAGES ============
BASE_URL = "https://monespace.marietton.com"
LOGIN_URL = BASE_URL + "/"
PLANNING_URL = BASE_URL + "/rdva_dispo/PLATEAU"
SEL_USER = "input[name='login']"
SEL_PASS = "#password"
SEL_SUBMIT = "#submitButton"
COULEUR_DISPO = "lightgreen"   # vert = horaire disponible (bleu = tes rendez-vous)
JOURS_VEILLE = 7
INTERVALLE_MIN = 30

MODE = "boucle"
# ==================================

for _arg in ("test-mail", "debug", "boucle", "unique"):
    if f"--{_arg}" in sys.argv:
        MODE = _arg

try:
    DOSSIER = Path(__file__).resolve().parent
except NameError:  # Pyzo ne définit pas toujours __file__
    DOSSIER = Path(r"C:\Users\louis\OneDrive\Bureau\Permis moto")
STATE_FILE = DOSSIER / "creneaux_vus.json"
LOG_FILE = DOSSIER / "veille_moto.log"
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def charger_env():
    env = DOSSIER / ".env"
    if env.exists():
        for ligne in env.read_text(encoding="utf-8").splitlines():
            if "=" in ligne and not ligne.strip().startswith("#"):
                k, v = ligne.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    manquantes = [k for k in ("MOTO_USER", "MOTO_PASS", "SMTP_USER", "SMTP_PASS", "MAIL_TO")
                  if not os.getenv(k)]
    if manquantes:
        sys.exit(f"Variables manquantes dans .env : {', '.join(manquantes)}")


def log(msg):
    ligne = f"[{dt.datetime.now():%Y-%m-%d %H:%M}] {msg}"
    print(ligne)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(ligne + "\n")


def recuperer_html():
    with sync_playwright() as p:
        nav = p.chromium.launch(headless=True)
        page = nav.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.fill(SEL_USER, os.environ["MOTO_USER"])
        page.fill(SEL_PASS, os.environ["MOTO_PASS"])
        page.click(SEL_SUBMIT)
        page.wait_for_load_state("networkidle")
        page.goto(PLANNING_URL, wait_until="networkidle")
        html = page.content()
        if MODE == "debug":
            page.screenshot(path=str(DOSSIER / "debug_planning.png"), full_page=True)
            (DOSSIER / "debug_planning.html").write_text(html, encoding="utf-8")
            log("Capture enregistrée : debug_planning.png / debug_planning.html")
        nav.close()
    return html


def extraire_creneaux(html):
    """Lit la liste 'events:[...]' du calendrier et garde les créneaux verts."""
    m = re.search(r"events\s*:\s*(\[.*?\])\s*,?\s*\r?\n", html, re.S)
    if not m:
        raise RuntimeError("Liste des créneaux introuvable (connexion échouée ou page modifiée ?)")
    creneaux = []
    for ev in json.loads(m.group(1)):
        if str(ev.get("color", "")).lower() != COULEUR_DISPO:
            continue
        debut = dt.datetime.strptime(ev["start"], "%Y-%m-%d %H:%M:%S")
        fin = dt.datetime.strptime(ev["end"], "%Y-%m-%d %H:%M:%S")
        creneaux.append({
            "id": ev["start"],
            "texte": f"{JOURS[debut.weekday()]} {debut:%d/%m} de {debut:%H:%M} à {fin:%H:%M}",
            "debut": debut,
            "lien": BASE_URL + ev["url"] if ev.get("url") else PLANNING_URL,
        })
    return creneaux


def envoyer_mail(creneaux):
    msg = EmailMessage()
    msg["Subject"] = f"Moto : {len(creneaux)} créneau(x) plateau disponible(s)"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    corps = f"Nouveaux créneaux plateau dans les {JOURS_VEILLE} prochains jours :\n\n"
    corps += "\n".join(f"- {c['texte']}\n  Réserver : {c['lien']}" for c in creneaux)
    corps += f"\n\nPlanning complet : {PLANNING_URL}"
    msg.set_content(corps)
    with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"), int(os.getenv("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


def verifier():
    maintenant = dt.datetime.now()
    limite = maintenant + dt.timedelta(days=JOURS_VEILLE)
    tous = extraire_creneaux(recuperer_html())
    proches = [c for c in tous if maintenant <= c["debut"] <= limite]

    vus = set(json.loads(STATE_FILE.read_text(encoding="utf-8"))) if STATE_FILE.exists() else set()
    nouveaux = [c for c in proches if c["id"] not in vus]
    STATE_FILE.write_text(json.dumps([c["id"] for c in proches]), encoding="utf-8")

    log(f"{len(tous)} créneau(x) dispo au total, {len(proches)} sous {JOURS_VEILLE} j, "
        f"{len(nouveaux)} nouveau(x)")
    if MODE == "debug":
        for c in tous:
            log(f"  {c['texte']}")
    if nouveaux:
        envoyer_mail(nouveaux)
        log("Alerte envoyée")


if __name__ == "__main__":
    charger_env()
    if MODE == "test-mail":
        envoyer_mail([{"texte": "Test - e-mail de vérification", "lien": PLANNING_URL}])
        log("E-mail de test envoyé")
    elif MODE == "boucle":
        log(f"Veille démarrée (toutes les {INTERVALLE_MIN} min)")
        while True:
            try:
                verifier()
            except Exception as e:
                log(f"Erreur : {e}")
            time.sleep(INTERVALLE_MIN * 60)
    else:
        verifier()