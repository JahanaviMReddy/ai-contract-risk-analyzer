import json
import pdfplumber
from functools import lru_cache
from typing import Dict, Any, List

# spaCy is optional; if unavailable, we fallback to a regex sentence splitter.
try:
    import spacy
    _has_spacy = True
except ImportError:
    spacy = None  # type: ignore
    _has_spacy = False

# langdetect is optional for language detection.
try:
    from langdetect import detect
    _has_langdetect = True
except ImportError:
    detect = None  # type: ignore
    _has_langdetect = False

# googletrans is optional for translation.
try:
    from googletrans import Translator
    _has_googletrans = True
except ImportError:
    Translator = None  # type: ignore
    _has_googletrans = False


# Display names for categories (used in structured output).
CLAUSE_TYPE_LABELS: List[str] = [
    "Payment Clause",
    "Termination Clause",
    "Liability Clause",
    "Confidentiality Clause",
    "Other",
]

# Multilingual keyword sets for clause matching.
# Add languages as needed: 'en', 'es', 'fr'.
CLAUSE_KEYWORDS_BY_LANG: Dict[str, Dict[str, List[str]]] = {
    "en": {
        "Payment Clause": [
            "payment", "pay", "fee", "fees", "invoice", "invoices",
            "due date", "amount", "price", "compensation", "billing",
        ],
        "Termination Clause": [
            "terminate", "termination", "cancel", "cancellation",
            "expire", "expiry", "end of term", "notice period",
        ],
        "Liability Clause": [
            "liability", "liable", "indemnify", "indemnification",
            "damages", "limit of liability", "limitation of liability",
        ],
        "Confidentiality Clause": [
            "confidential", "confidentiality", "disclose", "disclosure",
            "non-disclosure", "nda", "proprietary", "non-disclosure agreement",
        ],
    },
    "es": {
        "Payment Clause": [
            "pago", "pagos", "factura", "importe", "compensación", "precio",
        ],
        "Termination Clause": [
            "terminar", "rescindir", "cancelar", "caducar", "vencimiento",
            "plazo", "notificación",
        ],
        "Liability Clause": [
            "responsabilidad", "responsable", "indemnizar", "daños",
            "limitación de responsabilidad", "indemnización",
        ],
        "Confidentiality Clause": [
            "confidencial", "confidencialidad", "divulgar", "divulgación",
            "acuerdo de confidencialidad", "nda",
        ],
    },
    "fr": {
        "Payment Clause": [
            "paiement", "payer", "frais", "facture", "montant", "prix",
        ],
        "Termination Clause": [
            "résilier", "résiliation", "annuler", "expiration", "terme",
            "préavis", "fin de contrat",
        ],
        "Liability Clause": [
            "responsabilité", "responsable", "indemniser", "dommages",
            "limitation de responsabilité", "indemnisation",
        ],
        "Confidentiality Clause": [
            "confidentiel", "confidentialité", "divulguer", "divulgation",
            "accord de confidentialité", "nda",
        ],
    },
    "hi": {
        "Payment Clause": [
            "भुगतान", "शुल्क", "चालान", "मूल्य", "किराया", "राशि",
        ],
        "Termination Clause": [
            "समाप्त", "समाप्ति", "रद्द", "समाप्ति तिथि", "नोटिस",
        ],
        "Liability Clause": [
            "दायित्व", "जिम्मेदार", "हर्जाना", "क्षति", "परिपूर्ति",
        ],
        "Confidentiality Clause": [
            "गोपनीय", "गोपनीयता", "प्रकटीकरण", "गोपनीयता समझौता",
        ],
    },
    "kn": {
        "Payment Clause": [
            "ಪಾವತಿ", "ಶುಲ್ಕ", "ವಿಳಾಸ", "ಬೆಲೆ", "ಕಿರಾಯಿ", "ಮೊತ್ತ",
        ],
        "Termination Clause": [
            "ಕೊನೆ", "ರದ್ದು", "ಅಂತ್ಯ", "ಸೂಚನೆ", "ವಸ್ತುಮಾಹಿತಿ",
        ],
        "Liability Clause": [
            "ದಾಯ", "ಉತ್ತರದಾಯ", "ನಷ್ಟ", "ನಿರಪ್ಪಣೆ", "ಹಾನಿ",
        ],
        "Confidentiality Clause": [
            "ರಹಸ್ಯ", "ಗೌಪ್ಯತೆ", "ಪ್ರಕಟಣೆ", "ಗೌಪ್ಯತೆ ಒಪ್ಪಂದ",
        ],
    },
}

# Keywords that make a clause "risky" (lowercase).
RISKY_KEYWORDS: Dict[str, List[str]] = {
    "en": ["terminate", "penalty", "damages", "liability", "breach"],
    "es": ["terminar", "penalidad", "daños", "responsabilidad", "incumplimiento"],
    "fr": ["résilier", "pénalité", "dommages", "responsabilité", "violation"],
    "hi": ["समाप्त", "दायित्व", "चोट", "दोष", "उल्लंघन"],
    "kn": ["ಕೊನೆ", "ದಾಯ", "ಹಾನಿ", "ಉಲ್ಲಂಘನೆ", "ಜವಾಬ್ದಾರಿ"],
}

# Severity score per risk keyword (higher = more severe). Used for risk score calculation.
# Example: 1 risk keyword ≈ +20 base; severity adjusts (e.g. penalty/damages = +25).
RISKY_KEYWORD_SEVERITY: Dict[str, int] = {
    "terminate": 15,
    "breach": 20,
    "liability": 20,
    "penalty": 25,
    "damages": 25,
}

# Localized risk recommendation templates.
RISK_RECOMMENDATIONS: Dict[str, Dict[str, str]] = {
    "en": {
        "high": "High risk: clauses contain terminate, penalty, damages, liability, or breach. Involve legal counsel before signing.",
        "medium": "Moderate risk: review Liability and Termination clauses and all risk-flagged sentences.",
        "low": "Lower risk; still review classified clause types and any risk-flagged wording.",
        "none": "No significant risk keywords detected, but ensure a legal review before finalizing.",
    },
    "es": {
        "high": "Alto riesgo: las cláusulas contienen terminar, penalidad, daños, responsabilidad o incumplimiento. Involucre asesoría legal antes de firmar.",
        "medium": "Riesgo moderado: revise cláusulas de Responsabilidad y Terminación y todas las oraciones marcadas como riesgosas.",
        "low": "Bajo riesgo; aún revise los tipos de cláusulas clasificadas y cualquier redacción marcada como riesgosa.",
        "none": "No se detectaron palabras clave de riesgo significativas, pero realice una revisión legal antes de finalizar.",
    },
    "hi": {
        "high": "उच्च जोखिम: अनुच्छेदों में समाप्ति, दंड, हर्जाना, देनदारी, या उल्लंघन शामिल हैं। हस्ताक्षर करने से पहले कानूनी सलाह लें।",
        "medium": "मध्यम जोखिम: देनदारी और समाप्ति अनुच्छेदों की समीक्षा करें और सभी जोखिम-चिह्नित वाक्यों की जांच करें।",
        "low": "कम जोखिम; फिर भी वर्गीकृत अनुच्छेद प्रकारों और जोखिम-चिह्नित शब्दों की समीक्षा करें।",
        "none": "कोई महत्वपूर्ण जोखिम कुंजीशब्द नहीं पाया गया, लेकिन अंतिम निर्णय से पहले कानूनी समीक्षा सुनिश्चित करें।",
    },
    "kn": {
        "high": "ಉನ್ನತ ಜೋखिम: ವಾಕ್ಯಗಳಲ್ಲಿ ಕೊನೆಗೊಳ್ಳುವಿಕೆ, ದಂಡ, ನಷ್ಟ, ಜವಾಬ್ದಾರಿ, ಅಥವಾ ಉಲ್ಲಂಘನೆಗಳಿವೆ. ಸಹಿ ಮಾಡುತ್ತಿದ್ದಕ್ಕೆ ಮೊದಲು ಕಾನೂನು ಸಲಹೆ ಪಡೆಯಿರಿ.",
        "medium": "ಮಧ್ಯಮ ಜೋखिम: ಜವಾಬ್ದಾರಿ ಮತ್ತು ಕೊನೆಗೊಳ್ಳುವಿಕೆ ವಾಕ್ಯಗಳನ್ನು ಪರಿಶೀಲಿಸಿ ಮತ್ತು ಎಲ್ಲಾ ಅಪಾಯ ಗುರುತಿಸಿದ ವಾಕ್ಯಗಳನ್ನು ಪರಿಶೀಲಿಸಿ.",
        "low": "ಕಡಿಮೆ ಜೋखिम; ಇತ್ತೀಚೆಗೆ ವರ್ಗೀಕರಿಸಿದ ವಾಕ್ಯ ಪ್ರಕಾರಗಳು ಮತ್ತು ಅಪಾಯ ಗುರುತಿಸಿದ ಪದಗಳನ್ನು ಪರಿಶೀಲಿಸಿ.",
        "none": "ಉल्लೇಖನೀಯ ಅಪಾಯ ಕೀಲಿಮಣೆ ಪದಗಳು ಕಂಡುಬಂದಿಲ್ಲ, ಆದರೆ ಅಂತಿಮೀಕರಣಕ್ಕೂ ಮುಂಚೆ ಕಾನೂನು ಪರಿಶೀಲನೆಯನ್ನು ಖಾತ್ರಿಪಡಿಸಿ.",
    },
    "fr": {
        "high": "Risque élevé : les clauses contiennent résilier, pénalité, dommages, responsabilité ou violation. Consultez un conseiller juridique avant de signer.",
        "medium": "Risque modéré : examinez les clauses de responsabilité et de résiliation et toutes les phrases signalées à risque.",
        "low": "Risque faible ; examinez tout de même les types de clauses classées et toute formulation à risque.",
        "none": "Aucun mot-clé de risque significatif détecté, mais effectuez une révision juridique avant de finaliser.",
    },
}

# Contract type keywords for automatic contract categorization.
CONTRACT_TYPE_KEYWORDS: Dict[str, List[str]] = {
    "Employment Contract": [
        "employment", "employee", "employer", "salary", "job", "position",
        "working hours", "leave", "termination notice", "probation",
    ],
    "Freelance Contract": [
        "freelance", "contractor", "independent contractor", "invoice", "project",
        "scope", "deliverable", "hourly rate", "billing", "service provider",
    ],
    "Rental Agreement": [
        "rent", "tenant", "landlord", "lease", "security deposit", "lease term",
        "rent increase", "property", "occupancy", "premises",
    ],
    "NDA": [
        "confidential", "non-disclosure", "nondisclosure", "disclosure", "proprietary",
        "secret", "confidentiality", "indemnification", "non-use",
    ],
}

# AI-style explanations for each clause type (shown alongside clauses on results page) by language.
CLAUSE_EXPLANATIONS: Dict[str, Dict[str, str]] = {
    "en": {
        "Termination Clause": "This clause allows one party to end the contract.",
        "Payment Clause": "This clause explains how payments must be made.",
        "Liability Clause": "This clause defines responsibility for damages.",
        "Confidentiality Clause": "This clause sets out obligations to keep information confidential.",
        "Other": "General contract language.",
    },
    "es": {
        "Termination Clause": "Esta cláusula permite a una de las partes terminar el contrato.",
        "Payment Clause": "Esta cláusula explica cómo se deben realizar los pagos.",
        "Liability Clause": "Esta cláusula define la responsabilidad por daños.",
        "Confidentiality Clause": "Esta cláusula establece obligaciones de mantener la confidencialidad.",
        "Other": "Lenguaje contractual general.",
    },
    "fr": {
        "Termination Clause": "Cette clause permet à une partie de mettre fin au contrat.",
        "Payment Clause": "Cette clause explique comment les paiements doivent être effectués.",
        "Liability Clause": "Cette clause définit la responsabilité en cas de dommages.",
        "Confidentiality Clause": "Cette clause définit les obligations de confidentialité.",
        "Other": "Langage contractuel général.",
    },
}


@lru_cache(maxsize=1)
def get_nlp():
    """Return a spaCy NLP model if available, otherwise raise RuntimeError."""
    if not _has_spacy:
        raise RuntimeError(
            "spaCy is not installed. Install with: pip install spacy"
        )
    try:
        return spacy.load("en_core_web_sm")
    except OSError as exc:
        raise RuntimeError(
            "spaCy model 'en_core_web_sm' is not installed. "
            "Install it with: python -m spacy download en_core_web_sm"
        ) from exc


def extract_text_from_pdf(path: str) -> str:
    text_chunks: List[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text() or ""
            text_chunks.append(page_text)
    return "\n".join(text_chunks).strip()


def detect_language(text: str) -> str:
    """Detect language with langdetect (preferred), fallback to keyword matching."""
    if not text or not text.strip():
        return "en"

    text_low = text.lower()

    # heuristic based on clause keywords
    scores = {lang: 0 for lang in CLAUSE_KEYWORDS_BY_LANG.keys()}
    for lang, ct in CLAUSE_KEYWORDS_BY_LANG.items():
        for keywords in ct.values():
            for kw in keywords:
                if kw in text_low:
                    scores[lang] += 1
    heuristics = max(scores, key=scores.get)
    if scores[heuristics] == 0:
        # Additional weak matches if no exact clause keyword found
        if any(w in text_low for w in ["contrato", "arrendamiento", "empleado", "servicio", "factura"]):
            heuristics = "es"
        elif any(w in text_low for w in ["contrat", "confidentialité", "prestation", "frais", "locataire"]):
            heuristics = "fr"
        elif any(w in text_low for w in ["el ", "la ", "que ", "de "]):
            heuristics = "es"
        elif any(w in text_low for w in ["le ", "la ", "que ", "du ", "des "]):
            heuristics = "fr"
        else:
            heuristics = "en"

    # If we have a strong heuristic language, return it first.
    if heuristics and heuristics != "en":
        return heuristics

    # Then try langdetect as secondary option.
    if _has_langdetect and detect is not None:
        try:
            code = detect(text)
            if code.startswith("es"):
                return "es"
            if code.startswith("fr"):
                return "fr"
            if code.startswith("en"):
                return "en"
        except Exception:
            pass

    return heuristics


def translate_to_english(text: str, src_lang: str) -> str:
    """Translate text to English using googletrans if available."""
    if src_lang == "en" or not text or not text.strip():
        return text
    if _has_googletrans and Translator is not None:
        try:
            translator = Translator()
            result = translator.translate(text, src=src_lang, dest="en")
            return result.text
        except Exception:
            pass
    # fallback: return original (still will analyze via keyword heuristics)
    return text


def split_into_sentences(text: str) -> List[str]:
    """Split contract text into sentences."""
    if not (text and text.strip()):
        return []
    text = text.strip()

    if _has_spacy:
        try:
            nlp = get_nlp()
            doc = nlp(text)
            sentences = [sent.text.strip() for sent in doc.sents if sent.text.strip()]
            if sentences:
                return sentences
        except Exception:
            pass

    # Fallback: simple regex-based sentence split.
    import re

    splits = re.split(r"(?<=[.!?;])\s+|\n+", text)
    cleaned = [s.strip() for s in splits if s.strip()]
    if cleaned:
        return cleaned

    # Last ditch: split on all line breaks and semicolon as fallback.
    secondary = re.split(r"[\n;]+", text)
    return [s.strip() for s in secondary if s.strip()]


def _sentence_contains_any(sentence: str, keywords: List[str]) -> bool:
    lowered = sentence.lower()
    return any(kw in lowered for kw in keywords)


def _assign_clause_type(sentence: str, lang: str = "en") -> str:
    """
    Assign exactly one category to a sentence for the selected language.
    Priority: Payment → Termination → Liability → Confidentiality; else Other.
    """
    lowered = sentence.lower()
    clause_map = CLAUSE_KEYWORDS_BY_LANG.get(lang, CLAUSE_KEYWORDS_BY_LANG["en"])
    for clause_type, keywords in clause_map.items():
        if any(kw in lowered for kw in keywords):
            return clause_type
    return "Other"


SUGGESTION_TEMPLATES: Dict[str, str] = {
    "Liability Clause": "{base} Consider limiting liability to negligence or willful misconduct only.",
    "Termination Clause": "{base} Consider adding notice and cure periods for termination.",
    "Payment Clause": "{base} Consider specifying payment terms, due dates, and late fees.",
    "Confidentiality Clause": "{base} Consider clarifying scope, duration, and permitted disclosures.",
    "Other": "{base} Consider reviewing this clause for clarity and risk exposure.",
}


def _suggest_clause_improvement(sentence: str, clause_type: str) -> str:
    template = SUGGESTION_TEMPLATES.get(clause_type, SUGGESTION_TEMPLATES["Other"])
    base = sentence
    if len(base) > 120:
        base = base[:120].rstrip() + "..."
    return template.format(base=base)


def classify_clauses(sentences: List[str], lang: str = "en") -> List[Dict[str, Any]]:
    """
    Classify each sentence into one category, set risk_flag, and add explanation.
    Returns a list of dicts with keys: sentence, clause_type, risk_flag, explanation.
    Structured for JSON serialization.
    """
    result: List[Dict[str, Any]] = []
    risky_kw = RISKY_KEYWORDS.get(lang, RISKY_KEYWORDS["en"])
    for sent in sentences:
        clause_type = _assign_clause_type(sent, lang=lang)
        risk_flag = _sentence_contains_any(sent, risky_kw)
        explanation = CLAUSE_EXPLANATIONS.get(lang, CLAUSE_EXPLANATIONS["en"]).get(
            clause_type, CLAUSE_EXPLANATIONS[lang]["Other"] if lang in CLAUSE_EXPLANATIONS else CLAUSE_EXPLANATIONS["en"]["Other"]
        )
        suggestion = _suggest_clause_improvement(sent, clause_type)
        result.append({
            "sentence": sent,
            "clause_type": clause_type,
            "risk_flag": risk_flag,
            "explanation": explanation,
            "suggestion": suggestion,
        })
    return result


def detect_contract_type(text: str) -> str:
    """Detect high-level contract type by keyword matches."""
    if not text or not text.strip():
        return "General Contract"
    lower = text.lower()
    scores = {key: 0 for key in CONTRACT_TYPE_KEYWORDS}
    for type_name, keywords in CONTRACT_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower:
                scores[type_name] += 1
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "General Contract"


def detect_clause_types(sentences: List[str], lang: str = "en") -> Dict[str, List[str]]:
    """
    Group sentences by clause type (for backward compatibility).
    Uses same categories: Payment Clause, Termination Clause, etc.
    """
    result: Dict[str, List[str]] = {label: [] for label in CLAUSE_TYPE_LABELS}
    for sent in sentences:
        clause_type = _assign_clause_type(sent, lang=lang)
        result[clause_type].append(sent)
    return result


def detect_risky_clauses(sentences: List[str], lang: str = "en") -> List[str]:
    """
    Return sentences that contain any of the risky keywords, per language.
    """
    risky_kw = RISKY_KEYWORDS.get(lang, RISKY_KEYWORDS["en"])
    return [s for s in sentences if _sentence_contains_any(s, risky_kw)]


def _severity_for_sentence(sentence: str) -> int:
    """
    Return the severity contribution for one sentence: the maximum severity
    of any risk keyword found in it. Returns 0 if no risk keyword found.
    """
    lowered = sentence.lower()
    max_severity = 0
    for keyword, severity in RISKY_KEYWORD_SEVERITY.items():
        if keyword in lowered:
            max_severity = max(max_severity, severity)
    return max_severity


def compute_risk_score(risky_clauses: List[str]) -> int:
    """
    Calculate risk score as a percentage (0–100) based on:
    - Number of risky clauses (each risky clause contributes; example: 1 risk keyword ≈ +20)
    - Severity of keywords (penalty/damages = 25, liability/breach = 20, terminate = 15)
    Each risky clause adds the severity of its highest-severity keyword; total is capped at 100.
    """
    if not risky_clauses:
        return 0
    score = 0
    for sentence in risky_clauses:
        # Each risky clause contributes its keyword severity (min 15, max 25 per clause).
        # Example: 1 clause with "penalty" = +25; 4 such clauses = 100.
        severity = _severity_for_sentence(sentence)
        if severity == 0:
            # Clause flagged as risky but no severity mapped; use base +20
            severity = 20
        score += severity
    return min(100, score)


def analyze_contract_text(text: str, recommendation_language: str = "en") -> Dict[str, Any]:
    """
    Perform contract linguistic analysis:
    - Split into sentences
    - Classify each sentence into one category: Payment Clause, Termination Clause,
      Liability Clause, Confidentiality Clause, or Other
    - Set risk_flag per sentence (True if sentence contains risky keywords)
    Returns a structured JSON-friendly result with clause_classification (sentence, clause_type, risk_flag)
    plus summary, risk_score, risk_factors, recommendations for app compatibility.
    """
    if not (text and text.strip()):
        raise ValueError("No text provided for analysis.")

    # 0. Detect language and translate as needed.
    detected_language = detect_language(text)
    contract_type = detect_contract_type(text)
    translated_text = text
    if detected_language in ["hi", "kn"]:
        translated_text = translate_to_english(text, detected_language)

    sentences = split_into_sentences(translated_text)
    if not sentences:
        # Try simple line-based fallback for text-heavy contracts with no punctuation.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            sentences = lines
        else:
            sentences = [text.strip()]

    # 2. Classify each sentence: one category per sentence + risk_flag
    clause_classification = classify_clauses(sentences, lang=detected_language)

    # 3. Derived data: risky clauses and risk score (0–100)
    detected_clauses = detect_clause_types(sentences, lang=detected_language)
    risky_clauses = [c["sentence"] for c in clause_classification if c["risk_flag"]]
    risk_score = compute_risk_score(risky_clauses)

    # Build risk_factors for app/DB compatibility
    risk_factors: List[str] = []
    for clause_type in CLAUSE_TYPE_LABELS:
        list_sents = detected_clauses.get(clause_type, [])
        if list_sents:
            risk_factors.append(f"Detected {clause_type}: {len(list_sents)}")
    if risky_clauses:
        risk_factors.append(
            f"Sentences with risk_flag (keywords: terminate, penalty, damages, liability, breach): {len(risky_clauses)}"
        )
        for i, clause in enumerate(risky_clauses[:10], 1):
            excerpt = clause[:120] + ("..." if len(clause) > 120 else "")
            risk_factors.append(f"  Risky {i}: {excerpt}")
    if not risk_factors:
        risk_factors.append("No clause types or risky keywords identified in the extracted text.")

    lang = detected_language if detected_language in RISK_RECOMMENDATIONS else "en"
    rec_lang = recommendation_language if recommendation_language in RISK_RECOMMENDATIONS else "en"
    if risk_score >= 60:
        recommendations = [RISK_RECOMMENDATIONS[rec_lang]["high"]]
    elif risk_score >= 30:
        recommendations = [RISK_RECOMMENDATIONS[rec_lang]["medium"]]
    elif risk_score > 0:
        recommendations = [RISK_RECOMMENDATIONS[rec_lang]["low"]]
    else:
        recommendations = [RISK_RECOMMENDATIONS[rec_lang]["none"]]

    summary = (
        "Automated contract linguistic analysis completed. "
        "Each sentence was classified into a clause category and flagged for risk. "
        "This is assistive only and does not constitute legal advice."
    )

    return {
        "analysis_language": detected_language,
        "recommendation_language": rec_lang,
        "contract_type": contract_type,
        "translated_text": translated_text if translated_text != text else None,
        "clause_classification": clause_classification,
        "detected_clauses": detected_clauses,
        "risky_clauses": risky_clauses,
        "risk_score": risk_score,  # percentage 0–100
        "summary": summary,
        "risk_factors": risk_factors,
        "recommendations": recommendations,
    }


def get_clause_classification_json(text: str) -> str:
    """
    Return only the structured clause classification as a JSON string.
    Each item has: sentence, clause_type, risk_flag.
    """
    lang = detect_language(text)
    sentences = split_into_sentences(text)
    classification = classify_clauses(sentences, lang=lang)
    return json.dumps(classification, indent=2)


def analyze_contract_pdf(path: str) -> Dict[str, Any]:
    """Extract text from PDF with pdfplumber, then run linguistic analysis."""
    raw_text = extract_text_from_pdf(path)
    if not raw_text:
        raise ValueError("No text could be extracted from the PDF.")
    return analyze_contract_text(raw_text)
