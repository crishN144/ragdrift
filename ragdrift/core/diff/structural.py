"""Structural diff: chunk count delta, heading level changes, table detection."""
from typing import Any


def diff_chunk_count(before: int, after: int) -> dict[str, Any]:
    """Compare chunk counts and return delta info."""
    if before == 0:
        delta_pct = 100.0 if after > 0 else 0.0
    else:
        delta_pct = ((after - before) / before) * 100.0
    return {
        "before": before,
        "after": after,
        "delta": after - before,
        "delta_pct": round(delta_pct, 2),
        "severity": _chunk_severity(abs(delta_pct)),
    }


def _chunk_severity(abs_delta_pct: float) -> str:
    if abs_delta_pct > 50:
        return "critical"
    if abs_delta_pct > 20:
        return "high"
    if abs_delta_pct > 10:
        return "medium"
    if abs_delta_pct > 5:
        return "low"
    return "none"


def diff_headings(before: list[str], after: list[str]) -> dict[str, Any]:
    """Compare heading structures."""
    # Detect additions, removals, level changes
    changes = []
    before_set = set(before)
    after_set = set(after)

    removed = before_set - after_set
    added = after_set - before_set

    for h in removed:
        changes.append(f"removed: {h}")
    for h in added:
        changes.append(f"added: {h}")

    # Detect heading level shifts (e.g., H2 became H4)
    # Compare by heading text (strip #/number prefix)
    before_texts = {_heading_text(h): _heading_level(h) for h in before}
    after_texts = {_heading_text(h): _heading_level(h) for h in after}

    for text, before_level in before_texts.items():
        if text in after_texts:
            after_level = after_texts[text]
            if before_level != after_level:
                changes.append(f"level_shift: '{text}' H{before_level} \u2192 H{after_level}")

    severity = "none"
    if len(changes) > 3:
        severity = "high"
    elif len(changes) > 0:
        severity = "medium"

    return {
        "changes": changes,
        "headings_before": len(before),
        "headings_after": len(after),
        "severity": severity,
    }


def diff_tables(before_tables: list[str], after_tables: list[str]) -> dict[str, Any]:
    """Compare table structures (extraction-level tables)."""
    changes = []
    if len(before_tables) != len(after_tables):
        changes.append(f"table_count: {len(before_tables)} \u2192 {len(after_tables)}")

    # Check for broken tables (misaligned columns)
    for i, table in enumerate(after_tables):
        lines = [line for line in table.strip().split('\n') if line.strip()]
        if lines:
            col_counts = [line.count('|') for line in lines]
            if len(set(col_counts)) > 1:
                changes.append(f"table_{i}: column misalignment detected")

    severity = "medium" if changes else "none"
    return {"changes": changes, "severity": severity}


def diff_table_rows_in_chunks(ref_chunks: list[str], new_chunks: list[str]) -> dict[str, Any]:
    """Detect table structure changes by scanning chunk text for markdown table rows.

    This catches table corruption (broken pipes, misaligned columns, injected
    non-table lines) even when overall chunk count stays the same.
    """
    ref_stats = _table_stats_from_chunks(ref_chunks)
    new_stats = _table_stats_from_chunks(new_chunks)

    changes = []

    # Only flag misalignment that is NEW (not present in reference)
    new_misaligned = new_stats["misaligned_tables"] - ref_stats["misaligned_tables"]
    if new_misaligned > 0:
        changes.append(
            f"misaligned_columns: {new_misaligned} table(s) "
            f"have inconsistent pipe counts"
        )

    # Table rows added/removed
    ref_rows = ref_stats["total_table_rows"]
    new_rows = new_stats["total_table_rows"]
    if ref_rows != new_rows:
        changes.append(f"table_rows: {ref_rows} \u2192 {new_rows}")

    # Tables appearing or disappearing
    if ref_stats["table_count"] != new_stats["table_count"]:
        changes.append(
            f"table_count: {ref_stats['table_count']} \u2192 {new_stats['table_count']}"
        )

    # Severity: new misalignment is medium; large row delta is high
    severity = "none"
    if changes:
        if new_misaligned > 0:
            severity = "medium"
        if ref_rows > 0:
            delta_pct = abs(new_rows - ref_rows) / ref_rows
            if delta_pct > 0.2:
                severity = "high"

    return {
        "changes": changes,
        "severity": severity,
        "ref_table_rows": ref_rows,
        "new_table_rows": new_rows,
        "misaligned_tables": new_stats["misaligned_tables"],
    }


def _table_stats_from_chunks(chunks: list[str]) -> dict[str, Any]:
    """Count table rows and detect misaligned columns across all chunks."""
    total_rows = 0
    table_count = 0
    misaligned = 0

    for chunk in chunks:
        lines = chunk.split("\n")
        # A table row starts with | or has | in the middle of the line
        table_lines = [
            ln for ln in lines
            if ln.strip().startswith("|") and ln.count("|") >= 2
        ]

        if not table_lines:
            continue

        table_count += 1
        total_rows += len(table_lines)

        # Skip the separator row (e.g. |---|---|) when checking column counts
        data_lines = [
            ln for ln in table_lines
            if not all(c in "|- :" for c in ln.replace("|", ""))
        ]
        if data_lines:
            pipe_counts = [ln.count("|") for ln in data_lines]
            if len(set(pipe_counts)) > 1:
                misaligned += 1

    return {
        "total_table_rows": total_rows,
        "table_count": table_count,
        "misaligned_tables": misaligned,
    }


def _heading_level(heading: str) -> int:
    """Extract heading level from markdown heading or text heading."""
    heading = heading.strip()
    if heading.startswith('#'):
        return len(heading.split()[0])  # count #s
    return 0


def _heading_text(heading: str) -> str:
    """Extract heading text without level markers."""
    heading = heading.strip()
    if heading.startswith('#'):
        return heading.lstrip('#').strip()
    return heading
