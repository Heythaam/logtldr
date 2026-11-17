"""Drain3 template mining (AD-5b)."""

from logtldr.models import LogLine, Template


# TODO(LT-DRAIN): Replace with actual drain3 integration from AD-5b.
def template_logs(lines: list[LogLine]) -> tuple[list[Template], dict[int, str]]:
    """Mine templates. Stub returns one unique template per line."""
    templates = []
    assignments = {}
    for line in lines:
        t_id = f"t_{line.line_id}"
        templates.append(Template(
            template_id=t_id,
            pattern=line.message,
            count=1,
            first_occurrence_line_id=line.line_id,
            last_occurrence_line_id=line.line_id
        ))
        assignments[line.line_id] = t_id
    return templates, assignments