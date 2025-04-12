#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import subprocess
import time
import json
import re # Pour les expressions régulières (parsing filename)
import mutagen            # Lire/écrire métadonnées
import acoustid           # Utiliser 'acoustid' pour fingerprint/lookup
import musicbrainzngs     # Pour interagir avec l'API MusicBrainz
import requests           # Pour télécharger les pochettes
import questionary        # Pour l'interface interactive
from pathlib import Path  # Pour une manipulation plus facile des chemins de fichiers
import tempfile           # Pour stocker temporairement la pochette téléchargée
import shutil             # Pour copier/déplacer la pochette si nécessaire
import traceback          # Pour afficher les erreurs détaillées si besoin

# --- Configuration ---
ACOUSTID_API_KEY = os.environ.get('ACOUSTID_API_KEY')
USER_EMAIL_ADDRESS = os.environ.get('EMAIL_ADDRESS')
SCRIPT_NAME, SCRIPT_VERSION = "AudiothèqueMetadataWizard", 1.0
MUSICBRAINZ_USER_AGENT = f"{SCRIPT_NAME}/{SCRIPT_VERSION} ( {USER_EMAIL_ADDRESS} )"
MUSIC_DIR = Path.home() / "Musique"
FPCALC_PATH = None
AUDIO_EXTENSIONS = {'.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus'}
ACOUSTID_DELAY = 1.0 / 3.0
MUSICBRAINZ_DELAY = 1.1

# Tags essentiels à vérifier pour considérer un fichier comme "tagué"
ESSENTIAL_TAGS_MP3_ID3 = ['TIT2', 'TPE1', 'TALB'] # Titre, Artiste, Album (ID3)
ESSENTIAL_TAGS_VORBIS = ['title', 'artist', 'album'] # Pour FLAC, OGG (Vorbis Comments)
ESSENTIAL_TAGS_MP4 = ['©nam', '©ART', '©alb'] # Pour M4A/MP4

# --- Initialisation des API ---
try:
    musicbrainzngs.set_useragent(
        SCRIPT_NAME, f"{SCRIPT_VERSION}",
        contact=USER_EMAIL_ADDRESS #MUSICBRAINZ_USER_AGENT.split(' ')[-1].strip('()')
    )
except Exception as e:
    print(f"Erreur init musicbrainzngs: {e}", file=sys.stderr)
    sys.exit(1)

# --- Fonctions Utilitaires ---

def check_existing_metadata(filepath):
    """ Vérifie si les tags essentiels sont présents et non vides. """
    try:
        audio = mutagen.File(filepath, easy=False)
        if not audio:
            print(f"  AVERTISSEMENT: Impossible d'ouvrir {filepath} avec mutagen.", file=sys.stderr)
            return False

        tags_to_check = []
        tags_dict = None

        if isinstance(audio, mutagen.mp3.MP3):
            # print("  Détection: MP3 (ID3)") # Optionnel
            tags_to_check = ESSENTIAL_TAGS_MP3_ID3
            tags_dict = audio.tags if audio.tags else audio
        elif isinstance(audio, (mutagen.flac.FLAC, mutagen.oggvorbis.OggVorbis, mutagen.oggopus.OggOpus)):
            # print("  Détection: FLAC/OGG (Vorbis)") # Optionnel
            tags_to_check = ESSENTIAL_TAGS_VORBIS
            tags_dict = audio
        elif isinstance(audio, mutagen.mp4.MP4):
            # print("  Détection: MP4 (M4A/AAC)") # Optionnel
            tags_to_check = ESSENTIAL_TAGS_MP4
            tags_dict = audio
        else:
            print(f"  AVERTISSEMENT: Format non géré pour vérification tags: {type(audio)}", file=sys.stderr)
            return False

        if not tags_dict:
             print("  AVERTISSEMENT: Impossible de trouver le dictionnaire de tags.")
             return False

        missing_tags = []
        for tag_key in tags_to_check:
            if tag_key not in tags_dict:
                missing_tags.append(tag_key)
            else:
                tag_value = tags_dict[tag_key]
                has_content = False
                if isinstance(tag_value, list) and tag_value:
                    first_item = tag_value[0]
                    if hasattr(first_item, 'text') and first_item.text and first_item.text[0]: has_content = True
                    elif hasattr(first_item, 'strings') and first_item.strings and first_item.strings[0]: has_content = True
                    elif isinstance(first_item, str) and first_item: has_content = True
                if not has_content:
                     missing_tags.append(f"{tag_key} (vide)")

        if missing_tags:
            # print(f"  Tags manquants/vides: {', '.join(missing_tags)}.") # Optionnel, peut être verbeux
            return False

        print("  Tags essentiels déjà présents.")
        return True

    except Exception as e:
        print(f"ERREUR (check_existing_metadata) pour {filepath.name}: {e}", file=sys.stderr)
        # traceback.print_exc() # Décommenter pour debug détaillé
        return False # En cas d'erreur, on essaie de traiter

def get_fingerprint(filepath):
    """ Génère l'empreinte AcoustID et la durée via appel direct à fpcalc. """
    fpcalc_exe = f'{FPCALC_PATH}fpcalc' if FPCALC_PATH else 'fpcalc'
    command = [fpcalc_exe, "-json", str(filepath)]

    try:
        process = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8', errors='replace')
        result = json.loads(process.stdout)
        duration_str = result.get("duration")
        fingerprint = result.get("fingerprint")

        if duration_str is None or fingerprint is None:
             print(f"ERREUR: Sortie JSON fpcalc incomplète pour {filepath.name}", file=sys.stderr)
             print(f"Sortie reçue: {process.stdout[:200]}...", file=sys.stderr) # Limiter la sortie affichée
             return None, None

        duration = int(float(duration_str))
        # print(f"   Empreinte générée ({duration}s).") # Optionnel
        return duration, fingerprint

    except FileNotFoundError:
        print(f"ERREUR: Exécutable '{fpcalc_exe}' introuvable.", file=sys.stderr)
        return None, None
    except subprocess.CalledProcessError as e:
        print(f"ERREUR: fpcalc a échoué (Code: {e.returncode}) pour {filepath.name}", file=sys.stderr)
        # print(f"Stderr: {e.stderr}", file=sys.stderr) # Décommenter pour voir l'erreur fpcalc
        return None, None
    except json.JSONDecodeError as e:
         print(f"ERREUR: Analyse JSON fpcalc échouée: {e}", file=sys.stderr)
         print(f"Sortie reçue: {process.stdout[:200]}...", file=sys.stderr)
         return None, None
    except Exception as e:
        print(f"ERREUR inattendue (get_fingerprint) pour {filepath.name}: {e}", file=sys.stderr)
        return None, None

def lookup_acoustid(duration, fingerprint):
    """ Interroge l'API AcoustID pour obtenir des correspondances MusicBrainz. """
    if not duration or not fingerprint: return None
    try:
        # print("  Interrogation AcoustID...") # Optionnel
        response = acoustid.lookup(ACOUSTID_API_KEY, fingerprint, duration, meta="recordings releases releasegroups")
        time.sleep(ACOUSTID_DELAY)
        if response and response.get('status') == 'ok' and response.get('results'):
             best_result = response['results'][0] # Simplification: prendre le premier
             # print(f"  Résultat AcoustID trouvé (Score: {best_result.get('score', 0):.2f}).") # Optionnel
             return best_result
        else:
             # print(f"  Aucune correspondance AcoustID (Status: {response.get('status')}).") # Optionnel
             return None
    except acoustid.WebServiceError as e:
        print(f"ERREUR Service AcoustID: {e}", file=sys.stderr); time.sleep(ACOUSTID_DELAY); return None
    except Exception as e:
        print(f"ERREUR inattendue (lookup_acoustid): {e}", file=sys.stderr); time.sleep(ACOUSTID_DELAY); return None

def get_best_mbid_from_acoustid(acoustid_result):
    """ Extrait le premier Recording MBID pertinent du résultat AcoustID. """
    if not acoustid_result or not acoustid_result.get('recordings'):
        return None
    for rec in acoustid_result.get('recordings', []):
        if 'id' in rec:
            # print(f"  MBID trouvé via AcoustID: {rec['id']}") # Optionnel
            return rec['id']
    # print("  Aucun Recording ID utilisable dans le résultat AcoustID.") # Optionnel
    return None

def get_metadata_by_mbid(mbid):
    """ Récupère et parse les métadonnées détaillées depuis MusicBrainz via un Recording MBID. """
    print(f"  Interrogation MusicBrainz pour MBID: {mbid}...")
    try:
        rec_info = musicbrainzngs.get_recording_by_id(
            mbid,
            includes=['artists', 'releases', 'release-groups', 'artist-credits']
        )['recording']
        time.sleep(MUSICBRAINZ_DELAY)

        metadata = {'mbid': mbid}
        metadata['title'] = rec_info.get('title', '')
        artist_credits = rec_info.get('artist-credit', [])
        metadata['artist'] = ' & '.join([cred['artist']['name'] for cred in artist_credits if 'artist' in cred]) if artist_credits else ''

        release_list = rec_info.get('release-list', [])
        metadata['album'] = ''; metadata['release_id'] = None; metadata['year'] = ''; metadata['tracknumber'] = ''

        if release_list:
            release = release_list[0] # Simplification
            metadata['album'] = release.get('title', '')
            metadata['release_id'] = release.get('id', None)
            date_str = release.get('date', '')
            year = ''
            if date_str and len(date_str) >= 4 and date_str[:4].isdigit(): year = date_str[:4]

            if not year and release.get('release-group'):
                 try:
                     rg_id = release['release-group']['id']
                     # print(f"  Requête supp. pour année via RG ID: {rg_id}") # Optionnel
                     rg_info = musicbrainzngs.get_release_group_by_id(rg_id)['release-group']
                     time.sleep(MUSICBRAINZ_DELAY)
                     first_release_date = rg_info.get('first-release-date', '')
                     if first_release_date and len(first_release_date) >= 4 and first_release_date[:4].isdigit():
                          year = first_release_date[:4]
                 except musicbrainzngs.WebServiceError as e_rg: pass # Ignorer erreur silencieusement ?
                 except Exception: pass # Ignorer autres erreurs silencieusement ?
            metadata['year'] = year

        if metadata.get('title') and metadata.get('artist'):
            print(f"  Métadonnées complètes trouvées pour MBID {mbid}.")
            return metadata
        else:
            print(f"  Informations incomplètes pour MBID {mbid}.")
            return None

    except musicbrainzngs.WebServiceError as e:
        print(f"ERREUR Service MusicBrainz (MBID {mbid}): {e}", file=sys.stderr); time.sleep(MUSICBRAINZ_DELAY); return None
    except Exception as e:
        print(f"ERREUR inattendue (get_metadata_by_mbid pour {mbid}): {e}", file=sys.stderr); time.sleep(MUSICBRAINZ_DELAY); return None

def search_musicbrainz_by_text(artist_guess, title_guess):
    """ Cherche sur MusicBrainz par texte. Retourne une LISTE de dictionnaires de correspondances plausibles. """
    if not title_guess: return []

    query_parts = []
    safe_title = title_guess.replace('"', '\\"')
    query_parts.append(f'recording:"{safe_title}"')
    if artist_guess:
        safe_artist = artist_guess.replace('"', '\\"')
        query_parts.append(f'artist:"{safe_artist}"')
    query = " AND ".join(query_parts)

    print(f"  Recherche textuelle MusicBrainz: {query}...")
    possible_matches = []
    try:
        result = musicbrainzngs.search_recordings(query=query, limit=10, includes=['release-groups', 'artist-credits'])
        time.sleep(MUSICBRAINZ_DELAY)
        recordings = result.get('recording-list', [])
        if not recordings: return []

        # print(f"  {len(recordings)} résultat(s) brut(s) trouvé(s). Filtrage...") # Optionnel
        for rec in recordings:
            score = rec.get('score', 0)
            mbid = rec.get('id')
            title = rec.get('title', '')
            artist_credits = rec.get('artist-credit', [])
            artist_str = ' & '.join([c['artist']['name'] for c in artist_credits if 'artist' in c]) if artist_credits else ''
            first_release_title = ''; first_release_year = ''
            release_list = rec.get('release-list', [])
            if release_list:
                 first_release_title = release_list[0].get('title', '')
                 date_str = release_list[0].get('date', '')
                 if date_str and len(date_str) >= 4 and date_str[:4].isdigit(): first_release_year = date_str[:4]

            # Filtre de pertinence (Score > 80 et infos de base présentes)
            if score >= 80 and mbid and title and artist_str:
                 match_info = {'mbid': mbid, 'title': title, 'artist_str': artist_str, 'score': score,
                               'album_hint': first_release_title, 'year_hint': first_release_year}
                 possible_matches.append(match_info)
                 # print(f"    -> Candidat: {title} / {artist_str} (Score: {score})") # Optionnel

        possible_matches.sort(key=lambda x: x['score'], reverse=True)
        if not possible_matches: print("  Aucune correspondance textuelle jugée suffisante.")
        return possible_matches

    except musicbrainzngs.WebServiceError as e:
        print(f"ERREUR Service MusicBrainz (recherche texte): {e}", file=sys.stderr); time.sleep(MUSICBRAINZ_DELAY); return []
    except Exception as e:
        print(f"ERREUR inattendue (search_musicbrainz_by_text): {e}", file=sys.stderr); time.sleep(MUSICBRAINZ_DELAY); return []

def parse_filename(filename_stem):
    """ Tente d'extraire Artiste et Titre du nom de fichier (sans extension). """
    # print(f"  Analyse du nom de fichier: '{filename_stem}'") # Optionnel
    text = filename_stem
    patterns_to_remove = [
        r'\[[^\]]+\]$', r'\([^)]*official[^)]*\)', r'\([^)]*lyric[^)]*\)',
        r'\([^)]*audio[^)]*\)', r'\s*HD$', r'\s*4K$', r'^\d+\s*-\s*',
        r'\([^)]*visualizer[^)]*\)', r'\([^)]*music video[^)]*\)',
    ]
    for pattern in patterns_to_remove:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE).strip()

    parts = text.split(' - ', 1)
    metadata = {'title': '', 'artist': ''}
    if len(parts) == 2:
        metadata['artist'] = parts[0].strip()
        metadata['title'] = parts[1].strip()
        # print(f"  parse_filename: Trouvé 'Artiste - Titre' -> A: '{metadata['artist']}', T: '{metadata['title']}'") # Optionnel
        return metadata
    elif len(parts) == 1:
         metadata['title'] = text.strip()
         # print(f"  parse_filename: Pas de ' - ', mis tout dans Titre: '{metadata['title']}'") # Optionnel
         return metadata
    return None

def fetch_cover_art(release_mbid):
    """ Tente de télécharger la pochette depuis Cover Art Archive. Retourne le chemin temporaire. """
    if not release_mbid: return None
    caa_url = f"http://coverartarchive.org/release/{release_mbid}/front"
    print(f"  Tentative téléchargement pochette: {caa_url}")
    try:
        response = requests.get(caa_url, stream=True, timeout=15, headers={'User-Agent': MUSICBRAINZ_USER_AGENT, 'Accept': 'image/jpeg, image/png'})
        time.sleep(COVERART_DELAY)
        if response.status_code == 200:
            content_type = response.headers.get('Content-Type', 'image/jpeg').lower()
            suffix = '.jpg'
            if 'png' in content_type: suffix = '.png'
            elif 'jpeg' in content_type: suffix = '.jpg'

            # Utiliser NamedTemporaryFile pour garantir un nom unique et nettoyage auto si besoin
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode='wb')
            shutil.copyfileobj(response.raw, temp_file)
            temp_file.close() # Fermer le fichier pour que update_metadata puisse le lire
            print(f"  Pochette téléchargée dans {temp_file.name}")
            return temp_file.name
        elif response.status_code == 404: print("  Aucune pochette 'front' trouvée (404).")
        else: print(f"  Aucune pochette trouvée (Status: {response.status_code}).")
        return None
    except requests.exceptions.RequestException as e:
        print(f"ERREUR Téléchargement pochette: {e}", file=sys.stderr); return None
    except Exception as e:
        print(f"ERREUR inattendue (fetch_cover_art): {e}", file=sys.stderr); return None

def update_metadata(filepath, metadata, cover_path):
    """ Écrit les métadonnées et la pochette dans le fichier audio. """
    print(f"  Écriture métadonnées pour: {filepath.name}")
    saved_ok = False
    audio = None
    audio_full = None

    try:
        # Partie 1: Tags Texte (Easy)
        audio = mutagen.File(filepath, easy=True)
        if not audio:
             print(f"ERREUR: Impossible d'ouvrir {filepath.name} (easy) pour écriture.", file=sys.stderr)
             return False
        audio.delete()
        tag_map = {'title': 'title', 'artist': 'artist', 'album': 'album', 'date': 'year', 'tracknumber': 'tracknumber'}
        for easy_key, meta_key in tag_map.items():
             value = metadata.get(meta_key)
             if value:
                 try: audio[easy_key] = str(value)
                 except Exception: pass # Ignorer erreurs sur tags non supportés par easy
        audio.save()
        print("  Métadonnées texte sauvegardées (via easy=True).")

        # Partie 2: Pochette (Non-Easy)
        if cover_path and os.path.exists(cover_path):
            print("  Ajout/Mise à jour de la pochette...")
            audio_full = mutagen.File(filepath, easy=False)
            if not audio_full:
                print(f"ERREUR: Réouverture {filepath.name} (non-easy) échouée pour pochette.", file=sys.stderr)
                return True # Succès partiel (texte ok)

            with open(cover_path, 'rb') as f: cover_data = f.read()
            mime = 'image/jpeg' if cover_path.lower().endswith(('.jpg', '.jpeg')) else 'image/png'

            if isinstance(audio_full, mutagen.id3.ID3):
                if 'APIC:' in audio_full: del audio_full['APIC:'] # Supprimer anciennes
                audio_full.tags.add(mutagen.id3.APIC(encoding=3, mime=mime, type=3, desc='Cover', data=cover_data))
            elif isinstance(audio_full, mutagen.flac.FLAC):
                audio_full.clear_pictures()
                pic = mutagen.flac.Picture(); pic.data = cover_data; pic.type = 3; pic.mime = mime
                audio_full.add_picture(pic)
            elif isinstance(audio_full, mutagen.mp4.MP4):
                fmt = mutagen.mp4.MP4Cover.FORMAT_JPEG if mime == 'image/jpeg' else mutagen.mp4.MP4Cover.FORMAT_PNG
                audio_full['covr'] = [mutagen.mp4.MP4Cover(cover_data, imageformat=fmt)]
            elif isinstance(audio_full, (mutagen.oggvorbis.OggVorbis, mutagen.oggopus.OggOpus)):
                 import base64
                 encoded_data = base64.b64encode(cover_data).decode('ascii')
                 pic_value=f"data:{mime};base64,{encoded_data}"
                 if 'metadata_block_picture' in audio_full: del audio_full['metadata_block_picture']
                 audio_full['metadata_block_picture'] = [pic_value]
            else:
                print(f"  AVERTISSEMENT: Ajout pochette non géré pour {type(audio_full)}", file=sys.stderr)
                return True # Succès partiel

            audio_full.save()
            print("  Pochette ajoutée/mise à jour.")
        saved_ok = True # Atteint seulement si tout va bien ou juste texte ok

    except mutagen.MutagenError as e:
        print(f"ERREUR Mutagen (écriture): {e}", file=sys.stderr)
    except Exception as e:
        print(f"ERREUR inattendue (update_metadata) pour {filepath.name}: {e}", file=sys.stderr)
        # traceback.print_exc() # Pour debug
    finally:
        # Nettoyage pochette temp dans tous les cas après tentative
         if cover_path and os.path.exists(cover_path):
            try: os.remove(cover_path)
            except OSError: pass # Ignorer erreur si suppression échoue
    return saved_ok

# --- Fonction Principale ---
def process_music_library(music_dir):
    print(f"Scan interactif du dossier : {music_dir}")
    file_count = 0; processed_count = 0; skipped_count = 0; error_count = 0; tagged_count = 0
    stop_processing = False

    # Pas besoin de TemporaryDirectory global si NamedTemporaryFile est utilisé et nettoyé dans update_metadata
    # with tempfile.TemporaryDirectory() as temp_dir: ...

    try:
        all_files = sorted([p for p in music_dir.rglob('*') if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS])
        total_files = len(all_files)
        print(f"Trouvé {total_files} fichiers audio à vérifier.")
        if total_files == 0:
            print("Aucun fichier audio trouvé dans le dossier spécifié.")
            return

    except FileNotFoundError:
        print(f"ERREUR: Le dossier musical '{music_dir}' n'existe pas.", file=sys.stderr)
        return
    except Exception as e:
        print(f"ERREUR lors du listage des fichiers: {e}", file=sys.stderr)
        return


    for i, filepath in enumerate(all_files):
        if stop_processing: break
        print(f"\n--- Fichier {i+1}/{total_files}: {filepath.relative_to(music_dir)} ---")

        try:
            if check_existing_metadata(filepath):
                tagged_count += 1
                continue

            # --- Logique de recherche v1.4 ---
            suggested_metadata = None
            source_of_suggestion = "Aucune"
            suggested_cover_path = None
            cover_temp_file = None
            best_mbid_found = None
            action = None # Action utilisateur

            # 1. Essayer via empreinte
            print("-> Recherche via empreinte digitale...")
            duration, fingerprint = get_fingerprint(filepath)
            if fingerprint:
                acoustid_result = lookup_acoustid(duration, fingerprint)
                if acoustid_result:
                    best_mbid_found = get_best_mbid_from_acoustid(acoustid_result)
                    if best_mbid_found:
                         temp_metadata = get_metadata_by_mbid(best_mbid_found)
                         if temp_metadata:
                             suggested_metadata = temp_metadata
                             source_of_suggestion = "MusicBrainz (empreinte)"
                         else: best_mbid_found = None

            # 2. Si échec empreinte, essayer via nom de fichier -> Recherche Texte MB
            if not suggested_metadata:
                print("-> Recherche par empreinte échouée ou incomplète.")
                print("-> Tentative via nom de fichier...")
                parsed_info = parse_filename(filepath.stem)

                if parsed_info and (parsed_info.get('title') or parsed_info.get('artist')):
                    artist_guess = parsed_info.get('artist', '')
                    title_guess = parsed_info.get('title', '')
                    print("  -> Recherche textuelle MusicBrainz avec termes parsés...")
                    possible_matches = search_musicbrainz_by_text(artist_guess, title_guess)
                    selected_mbid = None

                    if len(possible_matches) == 1:
                         print("  -> Une seule correspondance textuelle trouvée, sélectionnée.")
                         selected_mbid = possible_matches[0]['mbid']
                         source_of_suggestion = "MusicBrainz (nom fichier)" # Provisoire

                    elif len(possible_matches) > 1:
                         print(f"  -> {len(possible_matches)} correspondances textuelles trouvées. Veuillez choisir :")
                         choices = []
                         for match in possible_matches:
                              display_text = f"{match['title']} - {match['artist_str']}"
                              if match.get('album_hint'): display_text += f" ({match['album_hint']})"
                              if match.get('year_hint'): display_text += f" [{match['year_hint']}]"
                              display_text += f" (Score: {match['score']})"
                              choices.append(questionary.Choice(title=display_text, value=match['mbid']))
                         choices.append(questionary.Separator())
                         choices.append(questionary.Choice("Aucune de ces propositions (utiliser nom fichier brut)", value="fallback_filename"))
                         choices.append(questionary.Choice("Aucune de ces propositions (saisie manuelle)", value="manual"))
                         choices.append(questionary.Choice("Passer ce fichier (Skip)", value="skip"))
                         choices.append(questionary.Choice("Arrêter le script", value="stop"))

                         user_choice = questionary.select("Quelle est la bonne correspondance ?", choices=choices).ask()

                         if user_choice == "fallback_filename": source_of_suggestion = "Nom de fichier (brut)"
                         elif user_choice == "manual": action = "manual"; source_of_suggestion = "Manuel"
                         elif user_choice == "skip": action = "skip"
                         elif user_choice == "stop" or user_choice is None: action = "stop"
                         else: selected_mbid = user_choice; source_of_suggestion = "MusicBrainz (choix utilisateur)" # Provisoire

                    # Récupérer détails si un MBID a été choisi/trouvé
                    if selected_mbid:
                        print(f"  -> Récupération des détails pour MBID choisi : {selected_mbid}")
                        temp_metadata = get_metadata_by_mbid(selected_mbid)
                        if temp_metadata:
                            suggested_metadata = temp_metadata
                            best_mbid_found = selected_mbid # Confirmer l'ID utilisé
                            # La source a été mise à jour lors du choix ou si len==1
                        else:
                            print(f"  ERREUR: Impossible de récupérer les détails pour MBID {selected_mbid}. Utilisation nom fichier brut.")
                            source_of_suggestion = "Nom de fichier (brut)"
                            best_mbid_found = None # Pas d'ID fiable finalement

                    # Si recherche texte vide OU fallback choisi OU erreur récupération détails MBID
                    if not suggested_metadata and source_of_suggestion != "Manuel" and action not in ["skip", "stop"]:
                         if title_guess or artist_guess: # Vérifier qu'on a qqc à proposer
                              print("  -> Utilisation des infos brutes du nom de fichier.")
                              suggested_metadata = {'title': title_guess, 'artist': artist_guess,'album': '','year': '','release_id': None}
                              source_of_suggestion = "Nom de fichier (brut)"
                              best_mbid_found = None
                         else: # Rien trouvé nulle part
                               source_of_suggestion = "Aucune"

                else: # Echec parsing nom de fichier
                    print("  -> Analyse du nom de fichier infructueuse.")
                    source_of_suggestion = "Aucune"

            # 4. Tentative pochette si source MB
            if suggested_metadata and source_of_suggestion.startswith("MusicBrainz"):
                 release_id = suggested_metadata.get('release_id')
                 if release_id:
                      cover_temp_file = fetch_cover_art(release_id)
                      if cover_temp_file: suggested_cover_path = cover_temp_file

            # --- Interaction Utilisateur (choix final) ---
            final_metadata = None
            current_cover_to_use = None

            if action in ["manual", "skip", "stop"]: pass # Action déjà déterminée
            elif suggested_metadata and source_of_suggestion != "Aucune":
                 print(f"\n--- Suggestion Finale (Source: {source_of_suggestion}) ---")
                 print(f"  Titre:   {suggested_metadata.get('title', 'N/A')}")
                 print(f"  Artiste: {suggested_metadata.get('artist', 'N/A')}")
                 if suggested_metadata.get('album'): print(f"  Album:   {suggested_metadata.get('album')}")
                 if suggested_metadata.get('year'): print(f"  Année:   {suggested_metadata.get('year')}")
                 if suggested_cover_path: print(f"  Pochette: Trouvée")
                 elif source_of_suggestion.startswith("MusicBrainz"): print("  Pochette: Non trouvée ou pas cherchée")
                 print("------------------------------------")
                 action = questionary.select(
                    "Action pour cette suggestion ?",
                    choices=[
                        questionary.Choice("✅ Accepter", value="accept"),
                        questionary.Choice("✏️ Modifier", value="modify"),
                        questionary.Choice("✍️ Saisir Manuellement", value="manual"),
                        questionary.Choice("➡️ Passer (Skip)", value="skip"),
                        questionary.Choice("🛑 Arrêter", value="stop"),
                    ], use_shortcuts=True ).ask()
            else:
                 print("\n--- Aucune suggestion finale ---")
                 action = questionary.select(
                    "Action pour ce fichier ?",
                    choices=[
                        questionary.Choice("✍️ Saisir Manuellement", value="manual"),
                        questionary.Choice("➡️ Passer (Skip)", value="skip"),
                        questionary.Choice("🛑 Arrêter", value="stop"),
                    ], use_shortcuts=True ).ask()

            # --- Traitement Action ---
            if action == "accept":
                final_metadata = suggested_metadata
                current_cover_to_use = suggested_cover_path
            elif action == "modify":
                modified_metadata = {}
                print("\n--- Modification ---")
                modified_metadata['title'] = questionary.text("Titre:", default=suggested_metadata.get('title', '')).ask()
                modified_metadata['artist'] = questionary.text("Artiste:", default=suggested_metadata.get('artist', '')).ask()
                modified_metadata['album'] = questionary.text("Album:", default=suggested_metadata.get('album', '')).ask()
                modified_metadata['year'] = questionary.text("Année:", default=suggested_metadata.get('year', '')).ask()
                if suggested_cover_path:
                     keep_cover = questionary.confirm("Conserver la pochette suggérée ?", default=True).ask()
                     current_cover_to_use = suggested_cover_path if keep_cover else None
                else: current_cover_to_use = None
                final_metadata = modified_metadata
            elif action == "manual":
                manual_metadata = {}
                print("\n--- Saisie Manuelle ---")
                manual_metadata['title'] = questionary.text("Titre:", default=suggested_metadata.get('title', '') if suggested_metadata else '').ask() # Pré-remplir ?
                manual_metadata['artist'] = questionary.text("Artiste:", default=suggested_metadata.get('artist', '') if suggested_metadata else '').ask()
                manual_metadata['album'] = questionary.text("Album:", default=suggested_metadata.get('album', '') if suggested_metadata else '').ask()
                manual_metadata['year'] = questionary.text("Année:", default=suggested_metadata.get('year', '') if suggested_metadata else '').ask()
                final_metadata = manual_metadata
                current_cover_to_use = None # Pas de pochette en manuel pour l'instant
            elif action == "skip": skipped_count += 1
            elif action == "stop" or action is None: stop_processing = True
            else: skipped_count += 1 # Cas par défaut

            # Nettoyer pochette temp si téléchargée mais non utilisée
            if suggested_cover_path and current_cover_to_use != suggested_cover_path and os.path.exists(suggested_cover_path):
                 print("  Nettoyage de la pochette suggérée non utilisée...")
                 try: os.remove(suggested_cover_path)
                 except OSError: pass

            # --- Écriture des métadonnées ---
            if final_metadata and action not in ["skip", "stop", None]:
                if any(final_metadata.get(k) for k in ['title', 'artist', 'album', 'year']):
                    print("  Application des métadonnées...")
                    if update_metadata(filepath, final_metadata, current_cover_to_use):
                         processed_count += 1
                    else:
                         error_count += 1
                else:
                     print("  Aucune donnée significative à écrire fournie. Fichier passé.")
                     skipped_count += 1
                     # Nettoyer pochette si associée à une action vide
                     if current_cover_to_use and os.path.exists(current_cover_to_use):
                          try: os.remove(current_cover_to_use)
                          except OSError: pass
            elif action == "skip": pass # Déjà compté
            elif action not in ["stop", None] and not final_metadata : # Cas où action = manuel mais rien saisi
                 print("  Saisie manuelle vide. Fichier passé.")
                 skipped_count += 1


        # Fin de la boucle principale (for filepath in all_files)
        except KeyboardInterrupt: # Gérer Ctrl+C proprement
            print("\nArrêt demandé par l'utilisateur (Ctrl+C).")
            stop_processing = True
        except Exception as loop_error: # Gérer erreur inattendue sur un fichier
            print(f"\nERREUR INATTENDUE sur le fichier {filepath.name}: {loop_error}", file=sys.stderr)
            traceback.print_exc() # Afficher la trace pour le debug
            error_count += 1
            # Proposer de continuer ?
            if not questionary.confirm("Une erreur s'est produite. Continuer avec le fichier suivant ?", default=True).ask():
                stop_processing = True


    # --- Rapport Final ---
    print(f"\n--- Rapport Final ---")
    print(f"Fichiers audio trouvés au total : {total_files}")
    print(f"Fichiers déjà tagués (ignorés) : {tagged_count}")
    print(f"Fichiers mis à jour avec succès : {processed_count}")
    print(f"Fichiers passés (Skip/Vide/Erreur non bloquante) : {skipped_count + error_count}")
    # Distinguer erreurs et skips ?
    # print(f"Fichiers passés (Skip/Vide) : {skipped_count}")
    # print(f"Erreurs / Non traités (empreinte/API/écriture) : {error_count}")


# --- Point d'entrée du script ---
if __name__ == "__main__":
    if ACOUSTID_API_KEY == 'VOTRE_CLE_API_ACOUSTID' or not ACOUSTID_API_KEY:
        print("ERREUR: Veuillez configurer votre clé API AcoustID (ACOUSTID_API_KEY).", file=sys.stderr)
        sys.exit(1)
    if "example.com" in MUSICBRAINZ_USER_AGENT or "/1.0" in MUSICBRAINZ_USER_AGENT: # Simple check
         print("ATTENTION: Veuillez configurer un User-Agent MusicBrainz spécifique (NomApp/Version et Contact).", file=sys.stderr)

    print("\n*** Assistant Interactif de Métadonnées Musicales v1.4 ***")
    print("Ce script scanne votre musique, cherche par empreinte, puis par nom")
    print("(avec recherche textuelle et choix si multiples correspondances),")
    print("et propose les infos brutes du nom en dernier recours.")
    print("\n!!! IMPORTANT !!! Faites une SAUVEGARDE de votre musique avant de continuer.")

    try:
        start_scan = questionary.confirm("Êtes-vous prêt à commencer le scan interactif ?", default=False, auto_enter=False).ask()
        if start_scan:
            process_music_library(MUSIC_DIR)
        else:
            print("Traitement annulé.")
    except KeyboardInterrupt:
        print("\nTraitement interrompu par l'utilisateur.")
    except Exception as main_err:
         print(f"\nERREUR GLOBALE INATTENDUE: {main_err}", file=sys.stderr)
         traceback.print_exc()

    print("\nScript terminé.")