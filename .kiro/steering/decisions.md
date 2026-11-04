# Décisions complémentaires

Ces décisions complètent requirements.md et design.md et l'emportent en cas de conflit.

## Périmètre et tests
- Tests hypothesis uniquement pour R1, R4, Normalized_Key (R5/R6), la classification Row_Outcome et l'idempotence du chargement. Le reste en tests unitaires simples.
- Types SQL portables, mais aucun test sur PostgreSQL. SQLite n'a pas REGEXP par défaut : écrire les CHECK avec length() et GLOB (ex. length(vin) = 20 AND vin NOT GLOB '*[^0-9A-Z]*').
- Optionnels, à faire en dernier : Req 33 (bonus) et Req 34.1 (test à 100 000 lignes).

## Questions humaines
- Pattern_Key larges, objectif une quinzaine de questions : R3 = champ + raison + modèle de libellé avec le nom d'ECU masqué (toutes les lignes Format_2 sans DID partagent une question) ; R5, R6, R7 = champ + raison.
- « Appliquer une valeur au groupe » seulement si le groupe est homogène (même valeur brute, même contexte). Pour R6, R7 et les champs vides de R8 : seulement « rejeter le groupe » et « cas par cas », plus « générer des diagnosticOpID déterministes » pour R7.
- Chaque question a un question_id distinct du Pattern_Key, avec parent_pattern_key, source_row_id, scope (group|row) et status. Une décision de portée row ne s'applique qu'à sa ligne aux Runs suivants.

## Journal, chargement, identité
- Decision_Records par champ écrits à l'évaluation ; le `_row` écrit seulement une fois le chargement connu (evaluated → loaded | load_rejected), dans la même transaction SQLite que le lot si possible. Un retry ne crée ni doublon ni second `_row`.
- Operation_Identity inclut aussi DiagToolName et DiagToolVersion.
- Rapport : couverture (lignes chargées / lignes source) distincte de l'Aligned_Percentage ; séparer rejected_out_of_scope, rejected_error, pending_human ; distinguer Run à froid (cache vide) et à chaud (cache rempli).

## Agent de résolution
- Toute proposition passe par un validateur déterministe (dictionnaire, regex, contrôles finaux) avant application.
- Auto-application au-dessus du seuil seulement pour les champs à dictionnaire fermé (originTool, ecuName, Vehmodel, ecuCode). ValueReference, dates et identifiants : toujours soumis à un humain.
- Priorité basse : outils en lecture seule (lookup_dictionary, find_similar_values, inspect_pattern_frequency, test_proposed_rule), 3 itérations max par Pattern_Key, appels d'outils tracés dans le journal.
- Créer data/synthetic_edge_cases.csv avec des variantes jamais vues (« Mégane E-Tech » accentué, « CLIP », « Diag Tool », « AIR-BAG UNIT ») : le fichier de 10 000 lignes ne contient aucune Unknown_Variant.

## Continuité, phases, système B
- Retour à la phase précédente autorisé (4 → 3, 3 → 2) sur demande explicite d'un opérateur humain, journalisé.
- Phases 1 à 3 : l'Orchestrator renvoie la vue normalisée des lignes legacy MIGRATE/CORRECT (les mêmes expected records que le Reconciler), pour une réponse identique avant et après bascule.
- Réconciliation : rapprocher par diagnosticOpID puis comparer les valeurs ; compter missing_in_target, unexpected_in_target, value_mismatch, duplicate_in_target. Une comparaison impossible = échec pour la porte de bascule.
- Approbation de la bascule : afficher la couverture et le résumé des exclusions ; l'approbation vaut validation de ces exclusions.
- Client_App : jeu fixe de VIN de contrôle avec réponses attendues ; deux indicateurs distincts, disponibilité et parité fonctionnelle. Un VIN de contrôle en 404 = échec de parité.
- Legacy B a son propre format (CHASSIS_NO, MODELE en casse mixte ex. « Megane E-Tech », DATE_MAJ en aaaammjj), traduit par un agent API. Données simulées à partir des VIN du fichier, avec une quinzaine de conflits plantés listés dans un fichier de contrôle.
- R6 : si Vehmodel est illisible et que le VIN_Model_Index ne donne pas un modèle unique, interroger Legacy B avant l'humain. Si Legacy B et la ligne donnent deux modèles valides différents : ASK_HUMAN (R7). Le Validation_Target reste calculé sans Legacy B.