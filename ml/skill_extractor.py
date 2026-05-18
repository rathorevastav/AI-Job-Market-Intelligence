"""
ml/skill_extractor.py

NLP skill extraction pipeline.

EXTRACTION FLOW:
    For each unprocessed job:
      1. Merge existing tag-based skills (already in Job.skills from scraper)
      2. Clean and tokenize title + description text
      3. Run spaCy NLP for entity recognition and noun chunk extraction
      4. Match tokens and n-grams against KNOWN_SKILLS lookup
      5. Apply SKILL_ALIASES normalization
      6. Filter noise words
      7. Score confidence per skill
      8. Merge all sources, deduplicate, sort by confidence
      9. Write merged skills back to Job.skills via crud.update_job_skills()
     10. Mark job as processed via crud.mark_skills_extracted()

HOW TO RUN:
    python -m ml.skill_extractor

    # Process a limited number of jobs:
    python -m ml.skill_extractor --batch-size 50 --max-batches 5

INSTALL:
    pip install spacy
    python -m spacy download en_core_web_sm
"""

from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from typing import Optional

import database.crud as crud
from database.connection import get_db_session
from database.models import Job
from ml.constants import (
    KNOWN_SKILLS,
    NOISE_WORDS,
    SKILL_ALIASES,
    SKILL_EXTRACTION_BATCH_SIZE,
)
from ml.utils import (
    batched,
    clean_text,
    log_pipeline_summary,
    timed,
    tokenize,
    truncate,
    utcnow,
)

logger = logging.getLogger(__name__)

# spaCy is imported lazily inside _load_nlp() so the module can be imported
# even when spaCy is not installed — useful in test environments.
_nlp = None


# ============================================================================
# NLP MODEL LOADER
# ============================================================================

def _load_nlp():
    """
    Loads the spaCy English model exactly once (singleton pattern).

    WHY en_core_web_sm?
        The small model (sm) is 12MB and fast. It provides:
        - Tokenizer
        - POS tagger (noun phrase detection)
        - Named entity recognizer (NER)
        We do not need word vectors (md/lg) because our extraction is
        dictionary-lookup based, not semantic similarity based.

    WHY NOT reload on every batch?
        Loading a spaCy model takes ~0.3s. Reloading it for every 50-job
        batch over 1000 jobs wastes 6+ seconds. Singleton avoids that.
    """
    global _nlp
    if _nlp is not None:
        return _nlp

    try:
        import spacy
        _nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
        # We disable 'ner' and 'parser' because:
        # - NER in en_core_web_sm is trained for people/places/orgs — not tech skills
        # - Parser (dependency tree) is expensive and unused in our pipeline
        # - We only need the tokenizer and POS tagger (for noun chunk detection)
        # Re-enable 'ner' if you later add entity-based extraction.
        logger.info("spaCy model 'en_core_web_sm' loaded (parser+ner disabled)")
        return _nlp
    except OSError:
        logger.error(
            "spaCy model not found. Run: python -m spacy download en_core_web_sm"
        )
        raise
    except ImportError:
        logger.error("spaCy not installed. Run: pip install spacy")
        raise


# ============================================================================
# SKILL EXTRACTION — PURE FUNCTIONS
# ============================================================================

@dataclass
class ScoredSkill:
    """A skill candidate with a confidence score and extraction source."""
    name:       str
    confidence: float               # 0.0 – 1.0
    sources:    list[str] = field(default_factory=list)  # "tag", "title", "description"


def _normalize_skill(raw: str) -> Optional[str]:
    """
    Applies alias normalization and validates the result.
    Returns the canonical skill name, or None if it should be discarded.
    """
    token = raw.lower().strip()
    if not token or len(token) < 2:
        return None
    if token in NOISE_WORDS:
        return None
    # Apply alias map — maps "py" → "python", "k8s" → "kubernetes", etc.
    canonical = SKILL_ALIASES.get(token, token)
    return canonical


def _extract_from_tags(existing_tags: list[str]) -> list[ScoredSkill]:
    """
    Converts pre-existing scraper tags to ScoredSkill objects.

    Tags from the scraper (RemoteOK API tags) are HIGH confidence because
    the job poster explicitly chose them. We assign 0.95 confidence.
    The scraper already lowercased and noise-filtered them, but we
    re-normalize aliases here for consistency.
    """
    results: list[ScoredSkill] = []
    for tag in (existing_tags or []):
        normalized = _normalize_skill(tag)
        if normalized:
            results.append(ScoredSkill(
                name=normalized,
                confidence=0.95,
                sources=["tag"],
            ))
    return results


def _extract_from_text_spacy(text: str, source_label: str) -> list[ScoredSkill]:
    """
    Runs spaCy NLP on text and extracts skill candidates.

    STRATEGY:
        1. Tokenize with spaCy (handles contractions, punctuation correctly)
        2. Check every unigram token against KNOWN_SKILLS
        3. Check every bigram against KNOWN_SKILLS (catches "react native", "node.js")
        4. Check every trigram against KNOWN_SKILLS (catches "ruby on rails")
        5. Normalize aliases on all matches

    CONFIDENCE SCORING:
        - title text matches:       0.90 (short text, high signal-to-noise ratio)
        - description text matches: 0.75 (longer text, more context noise)
        - Matches in KNOWN_SKILLS:  base confidence
        - Matches only in ALIASES:  base confidence × 0.85 (slightly less certain)

    WHY N-GRAM MATCHING?
        Single tokens miss multi-word skills. "react native" must be matched
        as a bigram or "react" and "native" would both match separately as
        false positives.
    """
    nlp = _load_nlp()
    results: list[ScoredSkill] = []

    if not text:
        return results

    base_confidence = 0.90 if source_label == "title" else 0.75
    cleaned = clean_text(truncate(text, max_chars=5000))
    doc = nlp(cleaned)

    tokens = [token.text for token in doc if not token.is_space]

    # Build n-grams for 1, 2, and 3 word combinations
    ngrams: list[tuple[str, int]] = []  # (phrase, gram_size)
    for n in (1, 2, 3):
        for i in range(len(tokens) - n + 1):
            phrase = " ".join(tokens[i:i + n])
            ngrams.append((phrase, n))

    seen_in_doc: set[str] = set()

    for phrase, n in ngrams:
        # Skip very short single tokens that are likely noise
        if n == 1 and len(phrase) < 2:
            continue

        # Check KNOWN_SKILLS first (highest precision)
        if phrase in KNOWN_SKILLS:
            canonical = _normalize_skill(phrase)
            if canonical and canonical not in seen_in_doc:
                seen_in_doc.add(canonical)
                # Trigrams and bigrams that match exactly get a small confidence boost
                # because multi-word matches are more specific
                gram_boost = 0.05 * (n - 1)
                results.append(ScoredSkill(
                    name=canonical,
                    confidence=min(1.0, base_confidence + gram_boost),
                    sources=[source_label],
                ))
            continue

        # Check ALIASES (catches "py" → "python", "k8s" → "kubernetes")
        if n == 1 and phrase in SKILL_ALIASES:
            canonical = SKILL_ALIASES[phrase]
            if canonical not in seen_in_doc:
                seen_in_doc.add(canonical)
                results.append(ScoredSkill(
                    name=canonical,
                    confidence=base_confidence * 0.85,
                    sources=[source_label],
                ))

    return results


def extract_skills_for_job(job: Job) -> list[ScoredSkill]:
    """
    Runs the full extraction pipeline for a single Job object.

    MERGING STRATEGY:
        Three sources contribute skills, in priority order:
          1. Existing tags (confidence 0.95) — scraper provided, high signal
          2. Title extraction (confidence 0.90) — short, focused text
          3. Description extraction (confidence 0.75) — verbose, noisier text

        When the same skill appears in multiple sources, we keep the HIGHEST
        confidence score and merge the source labels for auditing.
        This prevents a tag + title match from double-counting.
    """
    # Source 1: existing scraper tags
    tag_skills = _extract_from_tags(job.skills or [])

    # Source 2: title
    title_skills = _extract_from_text_spacy(job.title or "", "title")

    # Source 3: description (truncated to 5000 chars inside the function)
    desc_skills = _extract_from_text_spacy(job.description or "", "description")

    # Merge: name → highest ScoredSkill
    merged: dict[str, ScoredSkill] = {}
    for candidate in tag_skills + title_skills + desc_skills:
        name = candidate.name
        if name not in merged:
            merged[name] = candidate
        else:
            existing = merged[name]
            # Keep highest confidence; merge source labels
            if candidate.confidence > existing.confidence:
                existing.confidence = candidate.confidence
            for src in candidate.sources:
                if src not in existing.sources:
                    existing.sources.append(src)

    # Sort by confidence descending; secondary sort by name for determinism
    return sorted(merged.values(), key=lambda s: (-s.confidence, s.name))


def skills_to_string_list(scored: list[ScoredSkill], min_confidence: float = 0.5) -> list[str]:
    """
    Converts ScoredSkill objects to a plain list of skill name strings.
    Filters out skills below the confidence threshold.

    Args:
        scored:         Output of extract_skills_for_job()
        min_confidence: Skills below this threshold are excluded.
                        0.5 is a reasonable default — excludes guesses
                        while keeping clear matches.
    """
    return [s.name for s in scored if s.confidence >= min_confidence]


# ============================================================================
# BATCH PROCESSING
# ============================================================================

@timed
def run_skill_extraction(
    batch_size: int = SKILL_EXTRACTION_BATCH_SIZE,
    max_batches: Optional[int] = None,
    min_confidence: float = 0.5,
) -> dict[str, int]:
    """
    Processes all pending jobs in batches.

    DESIGN:
        - Fetches one batch of unprocessed jobs at a time
        - Never loads all jobs into memory simultaneously
        - Each batch is committed as a single transaction
        - Failed individual jobs are logged and skipped — one bad job
          does not block the rest of the batch
        - Re-running is safe (idempotent): only jobs with
          is_skills_extracted=False are fetched

    Args:
        batch_size:     Jobs per batch (trades memory vs. DB round-trips)
        max_batches:    Stop after this many batches (None = run until done)
        min_confidence: Minimum confidence to include a skill

    Returns:
        Stats dict for logging and scheduler reporting.
    """
    stats = {
        "batches_processed": 0,
        "jobs_processed":    0,
        "jobs_failed":       0,
        "skills_extracted":  0,
    }

    batch_num = 0

    while True:
        if max_batches is not None and batch_num >= max_batches:
            logger.info("Reached max_batches=%d — stopping", max_batches)
            break

        with get_db_session() as db:
            pending = crud.get_jobs_pending_skill_extraction(db, batch_size=batch_size)

            if not pending:
                logger.info("No pending jobs — skill extraction complete")
                break

            logger.info(
                "Batch %d: processing %d jobs",
                batch_num + 1, len(pending),
            )

            processed_ids: list[int] = []
            batch_skill_count = 0

            for job in pending:
                try:
                    scored = extract_skills_for_job(job)
                    skill_names = skills_to_string_list(scored, min_confidence)

                    crud.update_job_skills(db, job.id, skill_names)
                    processed_ids.append(job.id)
                    batch_skill_count += len(skill_names)

                    logger.debug(
                        "Job %d | extracted %d skills: %s",
                        job.id, len(skill_names), skill_names[:5],
                    )

                except Exception as exc:
                    logger.error(
                        "Skill extraction failed for job id=%d: %s",
                        job.id, exc,
                    )
                    stats["jobs_failed"] += 1
                    # Do not add to processed_ids — job will be retried next run

            # update_job_skills already sets is_skills_extracted=True per job.
            # mark_skills_extracted is an additional bulk update for any
            # that succeeded but didn't go through update_job_skills.
            # Here it's redundant but kept for safety / explicit audit.
            if processed_ids:
                crud.mark_skills_extracted(db, processed_ids)

            stats["jobs_processed"]  += len(processed_ids)
            stats["skills_extracted"] += batch_skill_count
            stats["batches_processed"] += 1
            batch_num += 1

            logger.info(
                "Batch %d done | jobs=%d skills=%d",
                batch_num, len(processed_ids), batch_skill_count,
            )

    log_pipeline_summary("skill_extraction", stats)
    return stats


# ============================================================================
# CLI ENTRY POINT
# ============================================================================

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="NLP skill extraction pipeline")
    parser.add_argument("--batch-size",    type=int, default=SKILL_EXTRACTION_BATCH_SIZE)
    parser.add_argument("--max-batches",   type=int, default=None)
    parser.add_argument("--min-confidence", type=float, default=0.5)
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        stream=sys.stdout,
    )
    args = _parse_args()
    result = run_skill_extraction(
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        min_confidence=args.min_confidence,
    )
    print("\n── Skill Extraction Results ──")
    for k, v in result.items():
        print(f"  {k}: {v}")
