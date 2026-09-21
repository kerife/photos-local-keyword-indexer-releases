from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable


TAXONOMY_ID = "es-semantic-open-v3"
LEGACY_TAXONOMY_ID = "es-visible-v1"
LEGACY_TAXONOMY_SHA256 = "21df615d1bd663ca6fd9eef1a391f2063bf094fc780ebd56b42c9644e5361164"
CONTEXTUAL_TAXONOMY_ID = "es-visible-contextual-v2"
CONTEXTUAL_TAXONOMY_SHA256 = "8e4ab67a3abd7b2a920a4f06fa08e0bb84e8b503c1cc5253341057fd43e00701"
PREVIOUS_CONTEXTUAL_V2_HASHES = frozenset({
    # The prior policy version accepted English city names without translating
    # them before persistence. Keep manifests created with that digest
    # readable while new scans normalize location aliases to Spanish.
    "3d7f78e13468539477c066b2c3be7f9d36ea8e89f0883bab9c01e4c7a5f60202",
    # Keep manifests created before the Spanish inflection aliases were added
    # loadable while new scans use the updated policy digest.
    "a2f8e91a9530267f34779629163b940cb94e4b6cd9b868e6b2acbf4f3708e6b5",
    "7dcc996774bfd65304709e7780e5db2459155839a8a6b51cea9b4accfd04c994",
    "4252508c6f6d53204f0f87fb1d0d4556ed0994d43679ee0b5d30357ad2359ecc",
    "b64de1d4f034fdca0486f7e1aa069f20b48f15faae37a42177d747bb123b2399",
    "13d2b8d899e8c09e2d793a5460d64b27351db59c154f35d1ebf2689fdeac8950",
    "9ac40c98f2f305530c0ee84019a3c842141055d989d6efbe340ad5240c7e657f",
    "89e84d77aa55ea44f5c44c9bed718833f21aa93a94de784b28de216b5436fbb7",
    "8017fd4ce4ae2ccc50e83177e7ca7bca5b7f76568082426221d1b346b962c058",
    "f2e8a1f7f8017106e7c372ba7e44ec090923118260de6621c2d84de2bbf1d66e",
    "424e5219602489dbe099be2c0822134337c8c144dc68f2f250050a7f16178922",
    "34d707fded017e54d3a720ddec42839e1f10d4e63c2ede291f0fafafad79ee3a",
    "85d021ff10bef6b897449680b7982b430a2662fb92c5452a4dc856a25f65996a",
})
TAXONOMY = (
    "persona", "grupo", "retrato", "animal", "mascota", "perro", "gato", "ave", "pez", "caballo", "insecto",
    "interior", "exterior", "casa", "habitación", "cocina", "comedor", "sala", "oficina", "edificio",
    "arquitectura", "calle", "carretera", "puente", "parque", "jardín", "playa", "costa", "montaña", "bosque",
    "campo", "desierto", "lago", "río", "mar", "cielo", "ciudad", "pueblo", "sendero", "árbol", "flor",
    "planta", "hoja", "roca", "arena", "agua", "nieve", "lluvia", "nube", "niebla", "sol", "sombra",
    "amanecer", "atardecer", "noche", "automóvil", "bicicleta", "motocicleta", "tren", "avión", "barco",
    "autobús", "camión", "mesa", "silla", "cama", "sofá", "ventana", "puerta", "libro", "cuaderno", "mochila",
    "teléfono", "computadora", "pantalla", "cámara", "reloj", "lámpara", "botella", "taza", "plato", "juguete",
    "herramienta", "comida", "bebida", "fruta", "verdura", "pan", "pastel", "ropa", "sombrero", "calzado",
    "gafas", "lentes", "caminar", "correr", "nadar", "cocinar", "comer", "beber", "jugar", "leer", "escribir",
    "conversar", "posar", "conducir", "montar", "volar", "paisaje", "primer plano", "naturaleza", "horizonte",
    "reflejo", "silueta", "colorido", "blanco y negro", "texto", "documento", "letrero", "dibujo", "pintura",
    "escultura",
)
_SPANISH_EQUIVALENTS = {
    "florence": "Florencia",
    "italy": "Italia",
    "historic building": "edificio histórico",
    "italian art museum": "museo de arte italiano",
    "crowd of tourists": "multitud de turistas",
    "architectural landmark": "monumento arquitectónico",
    "stone facade": "fachada de piedra",
    "public square": "plaza pública",
    "whiteboard": "pizarra",
    "office cubicle": "cubículo de oficina",
    "wifi setup instructions": "instrucciones de configuración wifi",
    "wifi setup": "configuración wifi",
    "cisco router": "router Cisco",
    "paper airplane": "avión de papel",
    "red balloon": "globo rojo",
    "blue writing": "escritura azul",
    "dome": "cúpula",
    "gondola": "góndola",
    "gondolas": "góndolas",
    "edificios históricos": "edificio histórico",
    "edificios historicos": "edificio histórico",
    "cisco device": "dispositivo Cisco",
    "baby": "bebé",
    "crying": "llanto",
    "smile": "sonrisa",
    "smiling": "sonrisa",
    "happy expression": "expresión alegre",
    "sad expression": "expresión triste",
    "dog": "perro",
    "cat": "gato",
    "pet": "mascota",
    "glasses": "lentes",
    "eyeglasses": "lentes",
    "gafas": "lentes",
    "anteojos": "lentes",
    "estadio alfredo harp helu": "Estadio Alfredo Harp Helú",
    "diablos rojos del mexico": "Diablos Rojos del México",
    "venetian architecture": "arquitectura veneciana",
    "brick facades": "fachadas de ladrillo",
    "venetian canal": "canal veneciano",
    "basilica di santa maria della salute": "Basílica de Santa María de la Salud",
    "basilica della salute": "Basílica de Santa María de la Salud",
    "basilica of saint mark": "Basílica de San Marcos",
    "chiesa di san moise": "Iglesia de San Moisés",
    "chiesa di santa maria del giglio": "Iglesia de Santa María del Giglio",
    "church of san vidal": "Iglesia de San Vidal",
    "basilica of the frari": "Basílica de Santa María Gloriosa dei Frari",
    "church of santo stefano": "Iglesia de Santo Stefano",
    "chiesa di san zulian": "Iglesia de San Zulian",
    "rooftop": "azotea",
    "wi-fi setup poster": "cartel de configuración wifi",
    "wifi setup poster": "cartel de configuración wifi",
    "venezia": "Venecia",
    "venice": "Venecia",
    "nights": "noche",
}
_KNOWN_CONTEXTUAL_SINGLE_WORD_KEYS = frozenset({
    "amalfi", "disney", "epcot", "estados unidos", "florencia", "florida", "italia",
    "orlando", "venecia", "venice",
})
_KNOWN_VISIBLE_BRAND_KEYS = frozenset({"mobil super", "bbva"})
_KNOWN_SAFE_SINGLE_WORD_KEYS = frozenset({
    "alegre", "amarillo", "bebé", "canal", "cascada", "cúpula", "estación", "fachada", "florencia",
    "iluminación", "inexistente", "llanto", "locomotora", "pizarra", "plaza", "rojo", "sonrisa", "tranvía",
    "triste", "vegetación", "góndola", "góndolas",
})
_KNOWN_CONTEXTUAL_PHRASE_KEYS = frozenset({
    "canal grande",
    "estados unidos",
    "pabellón de italia",
    "parque temático",
    "route 66",
    "san marcos",
    "torre de san marcos",
})
_KNOWN_CONTEXTUAL_ENTITY_KEYS = frozenset({
    "diablos rojos del méxico",
    "estadio alfredo harp helú",
    "rocco",
    "roccy",
})
TAXONOMY_SHA256 = hashlib.sha256(
    json.dumps(
        {
            "base_terms": TAXONOMY,
            "prohibited_terms": sorted({
                "persona identificada", "nombre completo", "texto literal", "transcripción literal",
            }),
            "policy_mode": "open_semantic_without_literal_transcription",
            "human_composite_terms": sorted({"persona", "personas", "gente", "grupo", "retrato", "pasajero", "pasajeros"}),
            "rejects_coordinate_pattern": r"[+-]?\d{1,3}[.,]\d{1,8}",
            "spanish_equivalents": _SPANISH_EQUIVALENTS,
            "known_contextual_single_word_keys": sorted(_KNOWN_CONTEXTUAL_SINGLE_WORD_KEYS),
            "known_visible_brand_keys": sorted(_KNOWN_VISIBLE_BRAND_KEYS),
            "known_safe_single_word_keys": sorted(_KNOWN_SAFE_SINGLE_WORD_KEYS),
            "known_contextual_phrase_keys": sorted(_KNOWN_CONTEXTUAL_PHRASE_KEYS),
            "known_contextual_entity_keys": sorted(_KNOWN_CONTEXTUAL_ENTITY_KEYS),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
SUPPORTED_POLICY_DIGESTS = {
    LEGACY_TAXONOMY_ID: LEGACY_TAXONOMY_SHA256,
    CONTEXTUAL_TAXONOMY_ID: CONTEXTUAL_TAXONOMY_SHA256,
    TAXONOMY_ID: TAXONOMY_SHA256,
}
SUPPORTED_POLICY_HASHES = {
    LEGACY_TAXONOMY_ID: frozenset({LEGACY_TAXONOMY_SHA256}),
    CONTEXTUAL_TAXONOMY_ID: frozenset({
        CONTEXTUAL_TAXONOMY_SHA256,
        *PREVIOUS_CONTEXTUAL_V2_HASHES,
    }),
    TAXONOMY_ID: frozenset({
        TAXONOMY_SHA256,
        # Keep manifests created immediately before the bounded venue and
        # visible-eyewear vocabulary update readable.
        "6161cc0b17d6c4ec8e5f36c7ba686bdf035995893d7975f85d11a283bafe5146",
    }),
}


def canonical_keyword_key(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("keyword must be a string")
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def normalize_keyword_storage(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("keyword must be a string")
    return unicodedata.normalize("NFC", " ".join(value.split()))


_CANONICAL_ALLOWLIST = {canonical_keyword_key(term): term for term in TAXONOMY}
_PROHIBITED_TERMS = frozenset({
    "persona identificada", "nombre completo", "texto literal", "transcripción literal",
})
_PROHIBITED_KEYS = frozenset(canonical_keyword_key(term) for term in _PROHIBITED_TERMS)
_PERSON_NAME_TOKENS = frozenset(canonical_keyword_key(term) for term in {
    "aaron", "alejandro", "alex", "alexandra", "ana", "andrea", "ari", "carlos", "david", "diego",
    "elena", "fernando", "gabriel", "isabel", "john", "juan", "julia", "kevin", "laura", "lucía", "maria",
    "maría", "miguel", "patricia", "pedro", "roberto", "sofia", "sofía", "tomás", "victor", "víctor", "ximena",
    # Commonly recognized given names must not become contextual keywords;
    # allowing them would persist identities even when the model emits a
    # title-cased full name rather than a generic people label.
    "albert", "mickey",
})
_LANDMARK_NAME_PREFIXES = frozenset({"basílica", "basilica", "iglesia", "catedral", "palacio", "monumento"})
# A title-cased multiword candidate with no place/landmark marker is
# ambiguous, but is a common shape for a person's name.  Reject it
# conservatively so that an incomplete given-name blocklist cannot become a
# privacy boundary.  Single-word labels and lower-case descriptive phrases
# remain available to the visible-keyword policy.
_PLACE_NAME_MARKERS = frozenset({
    "avenida", "avenue", "basílica", "basilica", "bridge", "calle", "canal", "catedral", "chiesa",
    "church", "costa", "coast", "castillo", "castle", "isla", "island", "iglesia", "lago", "lake",
    "mar", "museo", "museum", "monumento", "palacio", "palace", "parque", "park", "piazza", "plaza",
    "puente", "río", "rio", "road", "san", "santa", "santo", "square", "street", "templo", "torre",
    "tower", "via", "estadio", "stadium",
})
_PLACE_NAME_CONNECTORS = frozenset({"a", "al", "da", "de", "del", "di", "do", "dos", "la", "las", "los", "of", "the"})
# A landmark marker is not, by itself, proof that a model-generated label is a
# place rather than a person's name (for example, ``Iglesia Juan``).  Keep the
# small set of curated names/aliases with personal-name tokens explicitly
# trusted; all other landmark-looking labels still pass when they do not
# contain a detected personal-name token (for example, ``torre de San Marcos``).
_KNOWN_LANDMARK_KEYS = frozenset({
    "basílica de santa maría de la salud",
    "basilica della salute",
    "basilica di santa maria della salute",
    "basilica of saint mark",
    "basílica de san marcos",
    "torre de san marcos",
    "chiesa di santa maria del giglio",
    "iglesia de santa maría del giglio",
})
_COORDINATE_PATTERN = re.compile(r"[+-]?\d{1,3}[.,]\d{1,8}")
_EMAIL_PATTERN = re.compile(r"(?i)(?:^|\s)[^\s@]+@[^\s@]+(?:\s|$)")
_URI_PATTERN = re.compile(r"(?i)(?:https?|ftp|mailto|tel)://?\S+|\bwww\.\S+")
_DOMAIN_PATTERN = re.compile(
    r"(?i)\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?:[/?#]\S*)?\b"
)
_DIGIT_RUN_PATTERN = re.compile(r"\d{3,}")
_VISIBLE_KEYWORD_PATTERN = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ0-9 .'\-]+")
_LITERAL_TEXT_PREFIXES = (
    "dice ",
    "se lee ",
    "texto literal ",
    "transcripción literal ",
    "nombre completo ",
)


def normalize_existing_keywords(keywords: Iterable[str]) -> list[str]:
    """Normalize existing storage without altering its order, casing, or multiplicity."""
    return [unicodedata.normalize("NFC", keyword) for keyword in keywords]


def _is_safe_visible_keyword(value: str) -> bool:
    key = canonical_keyword_key(value)
    if key in _KNOWN_LANDMARK_KEYS:
        return True
    if not key or key in _PROHIBITED_KEYS:
        return False
    if _VISIBLE_KEYWORD_PATTERN.fullmatch(value) is None:
        return False
    if _contains_private_or_ocr_channel(value):
        return False
    if any(character.isdigit() for character in value) and key not in _KNOWN_CONTEXTUAL_PHRASE_KEYS:
        return False
    if any(key.startswith(prefix) for prefix in _LITERAL_TEXT_PREFIXES):
        return False
    if _COORDINATE_PATTERN.search(key):
        return False
    words = set(key.split())
    if words & _PROHIBITED_KEYS:
        return False
    if _looks_like_unverified_title_case_name(value, words=words, key=key):
        return False
    # Defense in depth for the model contract: common given names are never
    # persisted as keywords. Place names remain possible (for example
    # ``San Marcos``) because they do not match this personal-name pattern.
    original_words = value.split()
    landmark_name = bool(words & _LANDMARK_NAME_PREFIXES)
    if landmark_name and key not in _KNOWN_LANDMARK_KEYS and words & _PERSON_NAME_TOKENS:
        return False
    if len(original_words) >= 2 and all(word[:1].isupper() for word in original_words) and not landmark_name:
        if canonical_keyword_key(original_words[0]) in _PERSON_NAME_TOKENS:
            return False
    return (landmark_name or not (len(words) >= 2 and bool(words & _PERSON_NAME_TOKENS))) and key not in _PERSON_NAME_TOKENS


def _looks_like_unverified_title_case_name(
    value: str,
    *,
    words: set[str],
    key: str,
) -> bool:
    """Reject ambiguous proper-name-shaped labels without place evidence."""
    if (
        key in _CANONICAL_ALLOWLIST
        or key in _KNOWN_LANDMARK_KEYS
        or key in _KNOWN_VISIBLE_BRAND_KEYS
        or key in _KNOWN_CONTEXTUAL_SINGLE_WORD_KEYS
        or key in _KNOWN_SAFE_SINGLE_WORD_KEYS
        or key in _KNOWN_CONTEXTUAL_ENTITY_KEYS
    ):
        return False
    if len(words) == 1:
        # Open semantic terms are requested in lower case.  An unknown
        # title-cased token is far more likely to be a copied proper name than
        # a useful inferred category, while lower-case concepts such as
        # ``oftalmología`` and ``formulario`` remain open.
        return value[:1].isupper()
    original_words = value.split()
    if not all(word[:1].isupper() and word[1:].islower() for word in original_words):
        return False
    # A trailing landmark marker does not make a preceding title-cased name
    # safe.  MapKit can return labels such as ``Xavier Something Museum``;
    # without this positional check the label would be persisted as a place
    # keyword even though its leading words are an unverified identity.
    normalized_words = [canonical_keyword_key(item) for item in original_words]
    marker_positions = [
        index for index, word in enumerate(normalized_words)
        if word in _PLACE_NAME_MARKERS
    ]
    if marker_positions and marker_positions[0] > 0:
        prefix = original_words[:marker_positions[0]]
        if len(prefix) >= 2:
            return True
    if marker_positions and marker_positions[0] == 0:
        title_run = 0
        for word, normalized in zip(original_words[1:], normalized_words[1:], strict=True):
            if normalized in _PLACE_NAME_CONNECTORS:
                title_run = 0
            elif word[:1].isupper() and word[1:].islower():
                title_run += 1
                if title_run >= 2:
                    return True
            else:
                title_run = 0
    return not bool(words & _PLACE_NAME_MARKERS)


def _contains_private_or_ocr_channel(value: str) -> bool:
    """Reject contact, network and identifier-shaped OCR before persistence.

    Short numeric place names such as ``Route 66`` remain valid.  A run of
    three digits or four digits spread across separators is treated as a
    likely date, address, phone number, document identifier or other OCR
    payload and is rejected conservatively.
    """
    if _EMAIL_PATTERN.search(value) or _URI_PATTERN.search(value) or _DOMAIN_PATTERN.search(value):
        return True
    digits = [character for character in value if character.isdigit()]
    return len(digits) >= 4 or _DIGIT_RUN_PATTERN.search(value) is not None


def is_safe_visible_keyword(value: str) -> bool:
    """Public fail-closed check used before model output can enter a manifest."""
    return _is_safe_visible_keyword(value)


def is_known_person_name_token(value: str) -> bool:
    """Return whether a token is a known literal person-name candidate."""
    return isinstance(value, str) and canonical_keyword_key(value) in _PERSON_NAME_TOKENS


def proposed_keywords(
    existing_keywords: Iterable[str],
    candidates: Iterable[str],
    *,
    maximum: int = 8,
    allowed_contextual_keywords: Iterable[str] = (),
) -> list[str]:
    """Return new visible candidates, filtering sensitive personal inferences."""
    if maximum < 0:
        raise ValueError("maximum must not be negative")
    seen = {canonical_keyword_key(value) for value in normalize_existing_keywords(existing_keywords)}
    proposals: list[str] = []
    limit = min(maximum, 8)
    # Kept in the public signature for manifest/workflow compatibility.  The
    # v3 policy is already open vocabulary, so context no longer grants a
    # separate membership exception.
    del allowed_contextual_keywords
    if limit == 0:
        return proposals
    for candidate in candidates:
        stored = normalize_keyword_storage(candidate)
        stored = _SPANISH_EQUIVALENTS.get(canonical_keyword_key(stored), stored)
        key = canonical_keyword_key(stored)
        if (
            stored
            and _is_safe_visible_keyword(stored)
            and key not in seen
        ):
            # Keep the model's visible wording for contextual terms (for
            # example ``Epcot``/``Italia``), while preserving the old canonical
            # spelling for the generic controlled vocabulary.
            proposals.append(_CANONICAL_ALLOWLIST.get(key, stored))
            seen.add(key)
            if len(proposals) == limit:
                break
    return proposals
