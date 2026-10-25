# GemZ Studio

Outil Windows de préparation et de mise en ligne des produits GemZ (Shopify, Etsy, eBay).

**Étape 1 (cette version) : le studio photo.** L'outil surveille le dossier d'EOS Utility, range les photos
par produit pendant la séance, applique un preset de retouche et le watermark GemZ (photos et vidéo 360),
puis range chaque produit dans son dossier avec un SKU. La publication arrive à l'étape 2.

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
préfixe (MFC1, MFC2…). Le champ SKU de la fiche permet d'en imposer un autre (ex. `FLLAB21`). À l'étape 2,
l'outil vérifiera que le SKU n'existe pas déjà sur Shopify avant de publier.

## Développement

```
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m gemz_studio
```
