# Démo complète depuis l'interface

Même scénario que [demo.md](demo.md), mais **sans aucune commande API** : tout se fait dans l'interface (http://127.0.0.1:8501). Seuls le démarrage et l'arrêt des services se font en PowerShell. Les chiffres attendus sont ceux de la démo API (fichier de 10 000 lignes) ; les identifiants et les horodatages changent à chaque exécution.

## 0. Démarrer les services

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
Remove-Item -Recurse -Force var\demo -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -VarDir var\demo
```

Ouvrez http://127.0.0.1:8501. Dans la barre latérale, remplacez **Votre nom** par `demo` (il est inscrit dans le journal à chaque action). Le bandeau d'accueil et les 5 étapes s'affichent ; l'étape 1 est en surbrillance.

Le Client_App (fenêtre séparée) simule une application métier : ses compteurs « availability » et « parity » ne doivent jamais afficher d'échec pendant toute la démo. Chaque ligne indique aussi quel système a répondu : `[LEGACY_A]` jusqu'à la phase 3, `[TARGET]` dès la phase 4 (et de nouveau `[LEGACY_A]` après un retour arrière).

## 1. Comprendre le fichier (étape 1, page Accueil)

1. Cliquez sur **✨ Analyser le fichier avec l'IA** (compter jusqu'à une minute) ou, sans IA, sur **Utiliser la correspondance de référence** (instantané).
2. Relisez le tableau *Colonne du fichier → Champ de destination*. Les avertissements jaunes signalent une différence avec la référence ; un message rouge signale un conflit (deux colonnes vers le même champ).
3. Sans conflit, cliquez sur **✅ Je valide cette correspondance**. Avec un conflit, le bouton est bloqué : relancez l'analyse ou prenez la référence.

Résultat attendu : l'étape 1 passe à ✓ et l'étape 2 devient l'étape en cours. Sans Ollama, un encadré indique « L'IA n'était pas disponible » et la référence est proposée.

## 2. Migration 1 (étape 2)

Cliquez sur **🚀 Lancer la migration**. Une barre de progression apparaît (environ 8 secondes). À la fin, les résultats s'affichent en clair :

```text
10 000 lignes analysées
6 927 migrées automatiquement
637 corrigées automatiquement
1 916 rejetées
520 en attente d'une décision humaine
Couverture : 7 564 lignes sur 10 000 (75,6 %)
```

La migration est **en pause** : 12 questions attendent. Aucun appel à l'IA n'est fait sur ce fichier (0 token) : il ne contient aucun cas inconnu. Ouvrez la page **Résultats** pour le détail (répartition des lignes, fiabilité, rôle de l'IA, rapport complet).

## 3. Répondre aux questions (étape 3, page Questions)

L'étape 3 affiche « 12 questions concernent 520 lignes » : cliquez sur **Répondre aux questions**. Les questions arrivent **une à la fois**, des plus grosses aux plus petites, avec une barre de progression. Chaque carte montre le champ, le problème, des lignes d'exemple et la raison pour laquelle l'outil n'a pas décidé seul.

| Question (champ · type) | Lignes | Votre choix |
|---|---|---|
| Creation_date · illisible | 151 | Rejeter tout le groupe |
| toolVersion · illisible | 138 | Rejeter tout le groupe |
| originTool · illisible | 124 | Rejeter tout le groupe |
| diagnosticFieId · identifiant partagé | 60 | **Générer les identifiants automatiquement** |
| ecuCode · format sans DID | 46 | Rejeter tout le groupe |
| ecuName · illisible | 30 | Rejeter tout le groupe |
| Vehmodel · conflit avec Legacy B | 20 | Rejeter tout le groupe |
| ecuCode · illisible (4 libellés différents) | 17, 14, 14, 13 | Rejeter tout le groupe |
| ecuCode · illisible (valeur vide, 2 lignes semblables) | 2 | **Appliquer une valeur à tout le groupe** → saisir `ID0001` |

Pour chaque carte : choisissez l'option, puis **Valider ma réponse**. La question suivante apparaît. Seul le groupe homogène (même valeur brute, même libellé) propose « Appliquer une valeur » ; si vous choisissez cette option sans saisir de valeur, l'interface vous le signale.

Après la 12e réponse : « Terminé : 12 question(s) traitée(s) ». Cliquez sur **Retour à l'accueil**.

## 4. Migration 2 sans doublons (étape 3, suite)

1. Sur l'Accueil, cliquez sur **▶️ Appliquer mes réponses**. Les lignes décidées par vos réponses sont migrées ; ce second traitement ne recrée rien de ce qui est déjà chargé.
2. Attendu : 6 927 migrées, 683 corrigées, 2 390 rejetées, **0** en attente ; couverture 7 610 lignes sur 10 000 (76,1 %). Les 12 questions sont fermées.
3. La page **Résultats** permet de comparer les deux traitements (liste « Traitement »). La vérification d'idempotence (relancer une campagne sur le même fichier) se fait par l'API : voir l'étape 4 de [demo.md](demo.md).

## 5. Vérifier puis basculer (étapes 4 et 5)

**Étape 4, Accueil ou page Bascule** : cliquez sur **🔍 Comparer les deux systèmes**. Attendu : « Identiques sur 45 dossiers comparés » (0 écart).

**Étape 5, page Bascule** :

1. Les quatre cartes de phase s'affichent, la phase 1 étant « vous êtes ici ». Les garde-fous (✅ / ⚠️) expliquent ce qui reste à faire.
2. Dans **Aller vers**, choisissez *2* puis **Demander ce changement** : « C'est fait : phase 2 ».
3. Choisissez *3* puis **Demander ce changement** : phase 3.
4. Cliquez sur **🔍 Lancer la comparaison** : 0 écart.
5. Choisissez *4* puis **Demander ce changement**. L'interface répond **« Approbation requise »** : la phase reste 3 et un cadre indique les exclusions que vous acceptez (7 610 lignes chargées, 990 hors périmètre, 1 400 en erreur).
6. Changez le champ **Votre nom** de la barre latérale en `lead` (l'approbation doit venir d'une personne), puis cliquez sur **✅ J'approuve la bascule** : « C'est fait : phase 4 ».

Si vous demandez la phase 4 avant la comparaison, ou avec une question ouverte, la demande est **⛔ refusée** avec la raison, et la phase ne change pas.

Pendant ces trois changements, la fenêtre du Client_App continue d'afficher 200 avec parité ; à partir de la phase 4, les réponses viennent du nouveau système.

## 6. Retour arrière

Remettez le nom `ops` dans la barre latérale. Sur la page Bascule, ouvrez **Revenir en arrière** puis cliquez sur **Revenir à la phase 3**. Attendu : « phase 3 » et le Client_App toujours sans échec (les lectures reviennent sur l'ancien système).

Ouvrez **Historique des changements** : quatre lignes : 1→2, 2→3, 3→4 (par `lead`), 4→3 *Retour arrière* (par `ops`).

### Changer d'avis (facultatif)

Page **Décisions** : chaque réponse donnée y est listée. **Annuler** une réponse l'oublie. Sur l'Accueil, **Relancer un traitement complet** (toujours disponible) retraite tout le fichier : la question annulée est reposée sur la page Questions, vous y répondez autrement, puis vous appliquez vos réponses. Refaites la comparaison (étape 4) avant de rebasculer.

## 7. Journal des décisions (page Journal)

Choisissez le premier traitement (le plus ancien) dans **Traitement**, puis :

- Onglet **🔎 Une ligne précise** : saisissez la ligne **4** : correction de `ecuName` (`E-PS` → `EPS`, règle R5), date convertie, outil `DiagTool` → `DDT2000`. Ligne **13** : rejetée (caractère interdit, R4). Ligne **2** : envoyée à un humain (valeur illisible, R8).
- Onglet **📋 Toutes les décisions** : filtrez par type de décision, auteur, champ (`ecuName`) ou règle (`R5`).
- Onglet **🤖 Ce que l'IA a consulté** : vide ici (aucun cas inconnu).

L'export CSV complet du journal n'existe que par l'API (voir l'étape 7 de [demo.md](demo.md)).

## 8. Arrêter

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
```

## Variante : cas limites avec l'IA

Pour voir l'IA à l'œuvre dans l'interface (Ollama avec `qwen2.5:7b` lancé) :

```powershell
powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
Remove-Item -Recurse -Force var\demo_agent -ErrorAction SilentlyContinue
powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1 -Source data\synthetic_edge_cases.csv -VarDir var\demo_agent -NoClient
```

1. Étape 1 : validez la correspondance (IA ou référence).
2. Étape 2 : **Lancer la migration**. Environ 2 minutes ; le fichier ne contient que 8 lignes mais 6 motifs inconnus que l'IA examine.
3. Résultats attendus (variables, car l'IA n'est pas déterministe) : 1 migrée, environ 5 corrigées, 0 rejetée, environ 2 en attente ; consommation d'environ 8 650 tokens. La page **Résultats** détaille le rôle de l'IA et la page **Journal → Ce que l'IA a consulté** liste les outils interrogés par l'IA.
4. Page **Questions** : les cartes affichent « 🤖 Suggestion de l'IA » avec sa confiance ; l'option correspondante est pré-sélectionnée, c'est vous qui décidez.

Sans Ollama (`-NoLLM`), un avertissement indique que l'IA est indisponible : les 7 cas inconnus partent vers les humains, sans suggestion.
