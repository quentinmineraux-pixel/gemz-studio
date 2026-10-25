# GemZ Studio

Outil Windows de préparation et de mise en ligne des produits GemZ (Shopify, Etsy, eBay).

**Étape 1 : le studio photo.** L'outil pilote le Canon (ou surveille le dossier d'EOS Utility), range les
photos par produit pendant la séance, applique un preset de retouche et le watermark GemZ (photos et vidéo
360), puis range chaque produit dans son dossier avec un SKU.

**Étape 2 : fiches, publication et synchro du stock.** Dès qu'un produit est traité, sa fiche est rédigée
(format des fiches gem-z.fr, en français, anglais, espagnol et allemand), il entre dans la file de
publication (Shopify d'abord, puis Etsy et eBay selon les cases cochées) et son stock est synchronisé entre
les trois plateformes tant que l'application est ouverte. Les connexions aux boutiques restent à brancher
(voir plus bas).

## Installer

1. Sur GitHub, onglet **Actions** → dernier passage vert de « Windows » → télécharger l'artefact
   **GemZ-Studio-Windows** (fichier zip).
2. Dézipper : on obtient `GemZ Studio.exe`. Le mettre où tu veux (Bureau, Documents…) et double-cliquer.
   Au premier lancement, Windows peut afficher « Windows a protégé votre ordinateur » : cliquer sur
   **Informations complémentaires** puis **Exécuter quand même** (l'application n'est pas signée).

## Premier réglage

Bouton **Réglages** :

- **Dossier surveillé** : le dossier de destination choisi dans EOS Utility
  (Préférences → Dossier de destination).
- **Dossier de sortie** : où ranger les produits traités.
- **Watermark** : le logo GemZ est fourni ; taille, opacité et couleur (original, noir, blanc) réglables,
  avec un aperçu sur la photo sélectionnée.
- **Retouche automatique** : luminosité et contraste appliqués à toutes les photos. Le réglage est conservé
  d'une ouverture à l'autre. Laisser à zéro pour un rendu strictement fidèle.
- **Médias par produit** : nombre de photos et de vidéos qui déclenche le passage automatique au produit
  suivant (8 photos + 1 vidéo par défaut).

## Piloter le Canon sans EOS Utility

GemZ Studio peut piloter directement le 6D Mark II avec le kit officiel Canon (EDSDK), que tu télécharges
toi-même sur le Canon Developer Portal (il n'est pas fourni avec l'outil, licence Canon).

1. Dézipper le kit Canon, par exemple dans `Documents\EDSDK`.
2. **Réglages** → Appareil photo → Mode « Pilotage direct Canon (EDSDK) », puis choisir ce dossier
   (l'outil trouve seul `EDSDK.dll`. Un kit 32 bits, comme la version 3.6.1, marche aussi : il est alors
   chargé par le petit module `gemz-canon32.exe` inclus dans GemZ Studio, et son journal est dans
   `%APPDATA%\GemZ Studio\canon32.log`).
3. Fermer EOS Utility, brancher le boîtier en USB et l'allumer : il se connecte tout seul, à chaque
   ouverture de GemZ Studio.
4. Les réglages (ISO, vitesse, ouverture, correction, balance des blancs et température) se choisissent
   en haut de l'écran Prise de vue. Pour tout régler d'ici, mets la molette du boîtier sur **M** ; tourner
   une molette du boîtier met aussi l'écran à jour. Un clic dans la visée fait la mise au point à cet
   endroit (autofocus du boîtier réglé sur « 1 collimateur », pas sur le suivi de visage).

Onglet **Prise de vue** :

- visée en direct, mise au point auto (AF) ou manuelle par pas (`<` plus près, `>` plus loin), loupe x5 / x10 ;
- **Déclencher la photo** (ou la touche Espace) : la photo part directement sur le PC, sans passer par la
  carte. Le déclenchement se fait sans AF, pour garder la même mise au point sur les 8 angles du plateau ;
- **Démarrer / Arrêter la vidéo** : le sélecteur du boîtier doit être sur la position vidéo. À l'arrêt, la
  vidéo est rapatriée puis effacée de la carte, sans clic supplémentaire ;
- **Récupérer les vidéos de la carte** : rapatrie et efface les vidéos restées sur la carte.

Le mode « Appareil simulé » permet d'essayer l'écran sans boîtier.

## Une séance

1. **Nouvelle séance** → choisir le type de produit (les mêmes types que les collections Shopify).
2. Prendre les photos : elles apparaissent sous le produit en cours (marqué ●). Après 8 photos et 1 vidéo,
   le produit suivant démarre tout seul. **Produit suivant** force la coupure quand le nombre varie.
3. Sur les photos : **Photo principale**, ◀ ▶ pour l'ordre, **Reprendre** (la prochaine photo prise
   remplacera celle sélectionnée), **Supprimer** (le fichier part dans `_rejets`, rien n'est effacé),
   **Aperçu final** (double-clic) pour voir le rendu avec retouche et watermark.
4. **Saisie rapide** : taper `malachite fibreuse congo 140g 8x6x5cm 164€` puis Entrée remplit la fiche.
   Pour une bague, ajouter la taille : `t54`.
5. **Enregistrer et traiter** : le traitement tourne en arrière-plan et l'outil passe au produit suivant.

La séance est sauvegardée en continu : si l'application est fermée, elle reprend au prochain lancement.

## Ce que produit le traitement

```
Dossier de sortie/
  2026-10-25_Malachite-fibreuse_Congo_MFC20/
    originaux/        copies intactes des fichiers du Canon
    web/              MFC20_01.jpg … MFC20_08.jpg, MFC20_video.mp4 (retouchés + watermark)
    produit.json      toutes les infos de la fiche, pour la publication
```

Le SKU reprend le format de la boutique : initiales du nom et du pays, puis le numéro suivant pour ce
préfixe (MFC1, MFC2…). Le champ SKU de la fiche permet d'en imposer un autre (ex. `FLLAB21`). L'import des
produits Shopify fait avancer les numéros, et un SKU déjà présent sur Shopify n'est jamais recréé.

## Fiches et publication

- **Fiche** : bouton « Fiche et publication » (ou double-clic dans « Catalogue et ventes »). Les textes
  fixes (intitulés, avertissement lithothérapie, note photo) sont déjà traduits ; l'IA ne rédige que
  l'aspect, l'anecdote, la lithothérapie, le SEO et les 13 tags Etsy, en un seul appel par produit.
  Sans clé IA, la fiche attend d'être complétée à la main (aspect et lithothérapie en français au minimum,
  anglais en plus pour Etsy et ebay.com).
- **Coût IA** : Claude Opus 5.5 par défaut (~0,08 € la fiche), Haiku 5.5 au choix pour ~0,002 €.
  Plafond mensuel réglable (5 € par défaut) : au-delà, plus aucun appel.
- **Prix** : Etsy et eBay = prix Shopify + majoration (Réglages → Prix et ventes), arrondi à ,90 ;
  ebay.com en dollars. Bagues : taille réelle + autres tailles FR avec supplément et délai.
- **File** : publication automatique, sans validation (choix du cahier des charges). Une erreur est
  réessayée de plus en plus tard ; trois échecs déclenchent une notification. Réglages → Synchro permet de
  mettre la publication en pause.
- **Synchro** : toutes les 3 minutes, les ventes des trois plateformes sont relevées, le stock est recopié
  partout (une pièce unique vendue sur Etsy passe épuisée sur Shopify et eBay ; une bague vendue sur Shopify
  disparaît dans toutes ses tailles). Une pièce vendue est archivée sur Shopify (récupérable, désactivable
  dans Réglages → Connexions). Au démarrage, les ventes faites pendant l'arrêt sont rattrapées avant toute
  publication. Notification sur le téléphone avec ntfy.
- **Shopify fait foi** : un stock, un prix ou un prix barré changé dans l'admin Shopify, ou un produit
  archivé, est repris dans le catalogue puis recopié sur Etsy et eBay (après deux contrôles d'affilée, pour
  ne jamais réagir à une vente pas encore relevée).
- **Prix et promotions** : le prix et le prix barré se changent dans la fiche ; ils partent dans la minute.
  Les autres tailles d'une bague gardent leur écart. Etsy et eBay n'affichent pas de prix barré.
- **Tags et filtres Shopify** : une mise à jour n'en retire jamais (collections intactes) et ne remet pas
  ceux retirés à la main. Si une sous-catégorie change dans la fiche, l'ancien tag reste sur Shopify et une
  alerte le signale dans l'onglet Activité.
- **Pièce importée de Shopify** : sa fiche garde le texte de la boutique tant qu'on ne la réécrit pas
  (« Rédiger avec l'IA » la passe au format GemZ en 4 langues, à partir des photos de la boutique).

## Catalogue et ventes

- **Catalogue** : mosaïque de photos ou liste (le choix est retenu), pastilles « En ligne », « À traiter »,
  « Erreurs », « Épuisées », « Fiche à compléter », recherche (Ctrl+F) par nom, SKU, pierre ou pays, tri par
  date, nom, prix ou stock. Un clic montre la pièce à droite (photos ◀ ▶, prix, état sur chaque plateforme
  avec le lien de l'annonce, derniers événements) ; double-clic ou Entrée ouvre la fiche, où
  « ◀ Précédente / Suivante ▶ » (Alt+← / Alt+→) passe d'une pièce à l'autre. Clic droit : voir l'annonce,
  publier aussi sur Etsy ou eBay, ouvrir le dossier des photos.
- **Fiche** : onglet Photos pour changer l'ordre, la photo principale, en retirer ou en ajouter (même
  retouche et même logo) ; à l'enregistrement, les photos sont remplacées sur les annonces.
- **Ventes** : totaux du jour, du mois et de l'année, vente moyenne et répartition par plateforme sur la
  période choisie, liste des ventes avec la photo de chaque pièce (Ctrl+2).
- **Activité** : le journal classé par jour, filtrable (erreurs, alertes, ventes, publications…), avec les
  lignes identiques regroupées (×N) (Ctrl+3).
- **Ventes sur le téléphone** : Réglages → Synchro et notifications → « Mes ventes sur le téléphone ».
  Scanner le QR code avec le téléphone (même Wi-Fi que le PC), puis l'ajouter à l'écran d'accueil. La page
  ne s'ouvre qu'avec le code secret de l'adresse et tant que GemZ Studio est ouvert. La première fois,
  Windows demande d'autoriser GemZ Studio sur le réseau : cocher « Réseaux privés ».

## Brancher les boutiques (à faire ensemble)

Tout se règle dans Réglages → Connexions. Les clés sont rangées chiffrées par Windows (secrets.bin), jamais
dans settings.json.

1. **Shopify** : créer une application dans le Dev Dashboard Shopify (droits produits, stock, commandes,
   traductions, publications), l'installer sur la boutique, coller l'adresse xxx.myshopify.com, l'identifiant
   client et le secret, puis « Tester la connexion ». Ensuite Catalogue et ventes → Outils → « Importer /
   mettre à jour depuis Shopify ».
2. **Etsy** : demander l'accès développeur (developers.etsy.com, application « personnelle »), puis coller
   la keystring et le shared secret, « Connecter Etsy », charger les profils d'expédition et de retour,
   choisir les catégories. Ensuite Catalogue et ventes → Outils → « Relier les annonces Etsy » (par SKU,
   photo puis titre).
   Une annonce Etsy épuisée dont la pièce est encore en stock ici (vendue avant la liaison) est proposée
   aussi : une fois reliée, la dernière pièce est retirée de Shopify.
3. **eBay** : créer le compte vendeur et le compte développeur, une application de production avec son
   RuName, coller les identifiants, « Connecter eBay », remplir les politiques et l'emplacement.
4. **Téléphone** : installer ntfy, s'abonner à un nom de canal secret, le coller dans Réglages → Synchro.

## Développement

```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m gemz_studio
```
