"""Plain-language vocabulary and the guided journey (pure functions, no Streamlit, easy to test)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

PHASES = {
    1: ("Ancien système seul", "Les applications lisent l'ancien système. Le nouveau système est vide ou en préparation."),
    2: ("Copie en parallèle", "Le nouveau système se remplit, mais les applications lisent toujours l'ancien système."),
    3: ("Vérification", "On compare les deux systèmes sur un échantillon. Les applications lisent toujours l'ancien."),
    4: ("Nouveau système", "Les applications lisent le nouveau système. Retour arrière possible à tout moment."),
}

QUESTION_OPTIONS = {
    "apply_value": ("Appliquer une valeur à tout le groupe", "Vous choisissez une valeur, elle est utilisée pour toutes les lignes concernées."),
    "reject_group": ("Rejeter tout le groupe", "Ces lignes ne sont pas migrées. Elles restent dans l'ancien système."),
    "case_by_case": ("Décider ligne par ligne", "Le groupe est découpé : une question par ligne."),
    "generate_op_id": ("Générer les identifiants automatiquement", "Un identifiant unique et stable (UUID v5) est créé pour chaque ligne."),
}

ACTIONS = {"MIGRATE": "Copié tel quel", "TRANSFORM": "Transformé", "CORRECT": "Corrigé",
           "REJECT": "Rejeté", "ASK_HUMAN": "Question posée"}
AUTHORS = {"rule": "Règle automatique", "llm": "IA", "human": "Humain"}
GATES = {"open_questions": "Toutes les questions ont une réponse",
         "control_sample_gaps": "L'échantillon de contrôle est identique dans les deux systèmes"}
RUN_STATUS = {"running": "En cours", "completed": "Terminé", "failed": "Échec", "mapping_pending": "Schéma à valider"}


@dataclass(frozen=True)
class Step:
    key: str
    number: int
    title: str
    hint: str
    status: str          # done | current | todo


STEP_DEFS = (
    ("schema", "Comprendre le fichier", "Associer chaque colonne à son champ de destination."),
    ("migrate", "Lancer la migration", "Les règles traitent les cas évidents."),
    ("questions", "Répondre aux questions", "Vous tranchez les cas ambigus."),
    ("verify", "Vérifier", "Comparer l'ancien et le nouveau système."),
    ("switch", "Basculer", "Les applications lisent le nouveau système."),
)


def _done_flags(*, mapping_ok: bool, runs: Iterable[Any], open_questions: int, paused: bool,
                aligned: bool, phase: int) -> dict[str, bool]:
    has_run = any(getattr(r, "status", None) == "completed" for r in runs)
    return {
        "schema": mapping_ok,
        "migrate": mapping_ok and has_run,
        "questions": has_run and open_questions == 0 and not paused,
        "verify": aligned,
        "switch": phase == 4,
    }


def build_steps(*, mapping_ok: bool, runs: Iterable[Any], open_questions: int, paused: bool,
                aligned: bool, phase: int) -> list[Step]:
    """Five steps; the first one not done is ``current``, the later ones are ``todo``."""
    done = _done_flags(mapping_ok=mapping_ok, runs=list(runs), open_questions=open_questions, paused=paused,
                       aligned=aligned, phase=phase)
    steps, current_found = [], False
    for i, (key, title, hint) in enumerate(STEP_DEFS, start=1):
        if done[key] and not current_found:
            status = "done"
        elif not current_found:
            status, current_found = "current", True
        else:
            status = "todo"
        steps.append(Step(key, i, title, hint, status))
    return steps


def phase_label(n: int) -> str:
    return f"Phase {n} · {PHASES[n][0]}" if n in PHASES else f"Phase {n}"


def percent(part: int, whole: int) -> float:
    return round(100 * part / whole, 1) if whole else 0.0
