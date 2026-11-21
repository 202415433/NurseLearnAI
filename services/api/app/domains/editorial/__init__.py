"""API éditoriale (étape 2.2.2-b) : brouillons, versions, éditions, relecture, publication contrôlée.

La base de données reste l'autorité (migrations 0005 et 0006) : chaque écriture est exécutée dans une
transaction qui déclare l'utilisateur de la session (`nl.actor_id`) ; PostgreSQL revérifie
habilitations, portée, indépendance, immuabilité et sources. L'API contrôle avant, pour des réponses
claires, et ne propose aucune route de contournement.
"""
