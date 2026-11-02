"""Phrases Alexa claires construites à partir des réponses EcoleDirecte.

Toutes les fonctions sont pures : elles reçoivent le JSON renvoyé par l'API et
retournent une phrase en français, sans appel réseau.
"""

from __future__ import annotations

import base64
import html
import re
from datetime import date, datetime, timedelta
from typing import Any

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]
MAX_CONTENT = 180


def day_label(day: date, today: date) -> str:
    if day == today:
        return "aujourd'hui"
    if day == today + timedelta(days=1):
        return "demain"
    return f"{JOURS[day.weekday()]} {day.day} {MOIS[day.month - 1]}"


def next_school_day(today: date) -> date:
    day = today + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def school_year(today: date) -> str:
    start = today.year if today.month >= 8 else today.year - 1
    return f"{start}-{start + 1}"


def _spoken_time(value: str) -> str:
    try:
        hours, minutes = (int(part) for part in value.strip()[-5:].split(":"))
    except ValueError:
        return value
    unit = "heure" if hours <= 1 else "heures"
    return f"{hours} {unit}" if minutes == 0 else f"{hours} {unit} {minutes:02d}"


def _subject(value: Any) -> str:
    name = str(value or "").strip()
    if not name:
        return "matière inconnue"
    # Les sigles courts (EPS, SVT) restent en majuscules pour être épelés.
    return name.capitalize() if len(name) > 4 else name


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _clean_text(value: Any, encoded: bool = False) -> str:
    text = str(value or "")
    if encoded:
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            pass
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_CONTENT:
        text = text[:MAX_CONTENT].rsplit(" ", 1)[0] + "…"
    return text


def _capitalize(text: str) -> str:
    """Majuscule initiale sans toucher au reste (str.capitalize minusculerait « EPS »)."""
    return text[:1].upper() + text[1:]


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " et " + parts[-1]


def _courses(result: dict[str, Any], day: date) -> list[dict[str, Any]]:
    data = result.get("data")
    if not isinstance(data, list):
        return []
    prefix = day.isoformat()
    courses = [
        course for course in data
        if isinstance(course, dict) and str(course.get("start_date", "")).startswith(prefix)
    ]
    return sorted(courses, key=lambda course: str(course.get("start_date")))


def describe_timetable(result: dict[str, Any], day: date, today: date) -> str:
    label = day_label(day, today)
    courses = _courses(result, day)
    if any(str(c.get("typeCours", "")).upper() == "CONGE" for c in courses):
        return f"{label.capitalize()}, c'est un jour de congé."
    active = [c for c in courses if not c.get("isAnnule")]
    cancelled = [c for c in courses if c.get("isAnnule")]
    if not active:
        message = f"Tu n'as pas cours {label}."
    else:
        first = _spoken_time(str(active[0].get("start_date", "")))
        last = _spoken_time(str(active[-1].get("end_date", "")))
        details = []
        for course in active:
            part = f"à {_spoken_time(str(course.get('start_date', '')))}, {_subject(course.get('matiere'))}"
            if course.get("salle"):
                part += f" en salle {course['salle']}"
            details.append(part)
        message = (
            f"{label.capitalize()}, tu as {_plural(len(active), 'cours', 'cours')}, "
            f"de {first} à {last}. {_capitalize(_join(details))}."
        )
    for course in cancelled:
        message += (
            f" Attention, le cours de {_subject(course.get('matiere'))} de "
            f"{_spoken_time(str(course.get('start_date', '')))} est annulé."
        )
    return message


def describe_next_class(result: dict[str, Any], now: datetime) -> str:
    for course in _courses(result, now.date()):
        if course.get("isAnnule"):
            continue
        start = str(course.get("start_date", ""))
        try:
            starts_at = datetime.strptime(start[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if starts_at > now:
            message = f"Ton prochain cours est {_subject(course.get('matiere'))}, à {_spoken_time(start)}"
            if course.get("salle"):
                message += f", en salle {course['salle']}"
            if course.get("prof"):
                message += f", avec {course['prof']}"
            return message + "."
    return "Tu n'as plus cours aujourd'hui."


def _homework_items(result: dict[str, Any], day: date) -> list[dict[str, Any]]:
    data = result.get("data")
    items: list[dict[str, Any]] = []
    if isinstance(data, dict) and isinstance(data.get("matieres"), list):
        # Format détaillé : cahierdetexte/AAAA-MM-JJ.awp
        for matiere in data["matieres"]:
            a_faire = matiere.get("aFaire")
            if not isinstance(a_faire, dict):
                continue
            items.append({
                "matiere": matiere.get("matiere"),
                "contenu": _clean_text(a_faire.get("contenu"), encoded=True),
                "effectue": bool(a_faire.get("effectue")),
                "interrogation": bool(matiere.get("interrogation")),
            })
    elif isinstance(data, dict) and isinstance(data.get(day.isoformat()), list):
        # Format résumé : cahierdetexte.awp indexé par date
        for devoir in data[day.isoformat()]:
            items.append({
                "matiere": devoir.get("matiere"),
                "contenu": "",
                "effectue": bool(devoir.get("effectue")),
                "interrogation": bool(devoir.get("interrogation")),
            })
    return items


def describe_homework(result: dict[str, Any], day: date, today: date) -> str:
    label = day_label(day, today)
    items = _homework_items(result, day)
    if not items:
        return f"Tu n'as aucun devoir pour {label}."
    todo = [item for item in items if not item["effectue"]]
    parts = [f"Pour {label}, tu as {_plural(len(items), 'devoir')}"]
    if len(todo) != len(items):
        parts[0] += f", dont {len(items) - len(todo)} déjà fait{'s' if len(items) - len(todo) > 1 else ''}"
    parts[0] += "."
    for item in items:
        sentence = f"En {_subject(item['matiere'])}"
        sentence += f" : {item['contenu']}" if item["contenu"] else " : un travail est à faire"
        if item["effectue"]:
            sentence += ", c'est déjà fait"
        parts.append(sentence.rstrip(".…") + ".")
    tests = [_subject(item["matiere"]) for item in items if item["interrogation"]]
    if tests:
        parts.append(f"Attention, il y a une évaluation en {_join(tests)}.")
    return " ".join(parts)


def describe_absences(result: dict[str, Any]) -> str:
    data = result.get("data")
    events = data.get("absencesRetards") if isinstance(data, dict) else None
    if not isinstance(events, list) or not events:
        return "Tu n'as aucune absence ni aucun retard enregistré cette année."
    absences = [e for e in events if str(e.get("typeElement", "")).lower() == "absence"]
    retards = [e for e in events if str(e.get("typeElement", "")).lower() == "retard"]
    unjustified = [e for e in events if not e.get("justifie")]
    message = f"Cette année, tu as {_plural(len(absences), 'absence')} et {_plural(len(retards), 'retard')}."
    if unjustified:
        verb = "n'est pas encore justifié" if len(unjustified) == 1 else "ne sont pas encore justifiés"
        message += f" {len(unjustified)} {verb}."
    else:
        message += " Tout est justifié."
    latest = max(events, key=lambda e: str(e.get("date", "")))
    when = latest.get("displayDate") or latest.get("date")
    if when:
        message += f" Le plus récent : {str(latest.get('typeElement', 'événement')).lower()} {when}."
    return message


def _sender(message: dict[str, Any]) -> str:
    sender = message.get("from")
    if not isinstance(sender, dict):
        return "un expéditeur inconnu"
    name = sender.get("name") or " ".join(
        str(sender.get(key, "")).strip() for key in ("civilite", "prenom", "nom")
    ).strip()
    return name or "un expéditeur inconnu"


def describe_messages(result: dict[str, Any]) -> str:
    data = result.get("data")
    messages = data.get("messages") if isinstance(data, dict) else None
    received = messages.get("received") if isinstance(messages, dict) else None
    if not isinstance(received, list) or not received:
        return "Ta messagerie est vide."
    received = sorted(received, key=lambda m: str(m.get("date", "")), reverse=True)
    unread = [m for m in received if not m.get("read")]
    if not unread:
        latest = received[0]
        return (
            "Tu n'as aucun nouveau message. Le dernier vient de "
            f"{_sender(latest)} : {latest.get('subject') or 'sans objet'}."
        )
    parts = [f"Tu as {_plural(len(unread), 'nouveau message', 'nouveaux messages')}."]
    for message in unread[:3]:
        parts.append(f"De {_sender(message)} : {message.get('subject') or 'sans objet'}.")
    if len(unread) > 3:
        parts.append(f"Et {len(unread) - 3} autres à lire sur École Directe.")
    return " ".join(parts)


def describe_grades(result: dict[str, Any], limit: int = 3) -> str:
    data = result.get("data")
    notes = data.get("notes") if isinstance(data, dict) else None
    if not isinstance(notes, list) or not notes:
        return "Tu n'as encore aucune note cette année."
    notes = sorted(
        notes, key=lambda n: str(n.get("dateSaisie") or n.get("date") or ""), reverse=True
    )
    parts = []
    for note in notes[:limit]:
        value = str(note.get("valeur", "")).strip()
        subject = _subject(note.get("libelleMatiere"))
        if not value or not any(char.isdigit() for char in value):
            spoken = f"{value.lower() or 'non noté'} en {subject}"
        else:
            spoken = f"{value} sur {note.get('noteSur') or 20} en {subject}"
        if note.get("devoir"):
            spoken += f", pour {str(note['devoir']).strip()}"
        parts.append(spoken)
    title = "Ta dernière note" if len(parts) == 1 else f"Tes {len(parts)} dernières notes"
    return f"{title} : " + " ; ".join(parts) + "."


def describe_assessments(result: dict[str, Any], today: date, horizon: int = 14) -> str:
    """Évaluations annoncées dans le cahier de texte résumé (indexé par date)."""
    data = result.get("data")
    found: list[tuple[date, str]] = []
    if isinstance(data, dict):
        for key, entries in data.items():
            try:
                day = date.fromisoformat(str(key))
            except ValueError:
                continue
            if not (today <= day <= today + timedelta(days=horizon)) or not isinstance(entries, list):
                continue
            for entry in entries:
                if isinstance(entry, dict) and entry.get("interrogation"):
                    found.append((day, _subject(entry.get("matiere"))))
    if not found:
        return f"Aucune évaluation n'est annoncée dans les {horizon} prochains jours."
    found.sort()
    parts = [f"{subject} {day_label(day, today)}" for day, subject in found[:5]]
    intro = "Tu as une évaluation" if len(found) == 1 else f"Tu as {len(found)} évaluations"
    return f"{intro} : {_join(parts)}."
