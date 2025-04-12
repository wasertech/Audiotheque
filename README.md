# Audiothèque

Audiothèque est un assistant interactif pour gérer et enrichir les métadonnées de vos fichiers audio. Il utilise des empreintes acoustiques et des services comme AcoustID et MusicBrainz pour identifier vos morceaux et ajouter des informations telles que le titre, l'artiste, l'album, l'année et même la pochette.

## Fonctionnalités

- Identification des morceaux via empreintes acoustiques (AcoustID).
- Recherche textuelle sur MusicBrainz en cas d'échec de l'empreinte.
- Téléchargement automatique des pochettes d'album depuis Cover Art Archive.
- Interface interactive pour confirmer ou modifier les métadonnées proposées.
- Support des formats audio courants : MP3, FLAC, M4A, AAC, OGG, OPUS.

## Prérequis

Avant de commencer, assurez-vous que les éléments suivants sont installés et configurés :

1. **Chromaprint**  
   Installez `Chromaprint`, qui est nécessaire pour générer les empreintes acoustiques.  
   - Sous Debian/Ubuntu :  
     ```bash
     sudo apt install chromaprint
     ```
   - Sous macOS :  
     ```bash
     brew install chromaprint
     ```
   - Sous Windows : Téléchargez et installez depuis [Chromaprint](https://acoustid.org/chromaprint).

2. **Clé API AcoustID**  
   Obtenez une clé API gratuite sur [AcoustID](https://acoustid.org/). Ajoutez cette clé dans un fichier `.env` à la racine du projet:
   ```env
   ACOUSTID_API_KEY=VOTRE_CLE_API_ACOUSTID
   ```

3. **Adresse e-mail pour MusicBrainz**  
   Fournissez une adresse e-mail valide pour interagir avec l'API MusicBrainz. Ajoutez-la également dans le fichier `.env`:
   ```env
   EMAIL_ADDRESS=VOTRE_ADRESSE_EMAIL
   ```

4. **Python 3.13 et environnement virtuel**  
   Assurez-vous que Python 3.13 est installé. Créez un environnement virtuel et installez les dépendances :
   ```bash
   python3 -m venv .direnv/python-3.13
   source .direnv/python-3.13/bin/activate
   pip install -r requirements.txt
   ```

## Installation

1. Clonez ce dépôt :
   ```bash
   git clone https://github.com/votre-utilisateur/audiotheque.git
   cd audiotheque
   ```

2. Configurez votre environnement :
   - Copiez le fichier `.env.example` en `.env`:
     ```bash
     cp .env.example .env
     ```
   - Remplissez les variables `ACOUSTID_API_KEY` et `EMAIL_ADDRESS` dans le fichier `.env`.

3. Activez l'environnement virtuel :
   ```bash
   source .direnv/python-3.13/bin/activate
   ```

4. Installez les dépendances :
   ```bash
   pip install -r requirements.txt
   ```

## Utilisation

1. Lancez le script principal :
   ```bash
   python audiothèque.py
   ```

2. Suivez les instructions interactives pour scanner votre bibliothèque musicale et enrichir les métadonnées.

3. Sauvegardez vos fichiers avant d'exécuter le script, car il modifie directement les fichiers audio.

## Dépendances

Les bibliothèques Python suivantes sont utilisées dans ce projet (voir `requirements.txt`):

- `questionary`: Interface utilisateur interactive.
- `mutagen`: Lecture et écriture des métadonnées audio.
- `pyacoustid` : Intégration avec AcoustID.
- `musicbrainzngs`: Interaction avec l'API MusicBrainz.
- `requests`: Téléchargement des pochettes d'album.

## Limitations

- Le script ne prend en charge que les formats audio courants (MP3, FLAC, M4A, AAC, OGG, OPUS).
- Assurez-vous que `fpcalc` (Chromaprint) est accessible dans votre `PATH`.

## Contribuer

Les contributions sont les bienvenues ! Si vous trouvez un bug ou souhaitez ajouter une fonctionnalité, ouvrez une issue ou soumettez une pull request.

## Licence

Ce projet est sous licence MIT. Consultez le fichier [`LICENSE`](LICENSE) pour plus d'informations.
