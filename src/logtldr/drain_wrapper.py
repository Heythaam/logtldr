"""Drain3 template mining (AD-5b)."""

from drain3 import TemplateMiner
from drain3.masking import MaskingInstruction
from drain3.template_miner_config import TemplateMinerConfig

from logtldr.models import LogLine, Template

# Regex patterns from AD-5b
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_HEX = r"0x[0-9a-fA-F]+"
_IP = r"(\d+\.){3}\d+"
_NUM = r"\b\d+\b"


def template_logs(lines: list[LogLine]) -> tuple[list[Template], dict[int, str]]:
    """Mine templates from the message field of LogLines.

    Instantiates a fresh TemplateMiner, adds masking instructions to hide
    variable numbers/hex/UUIDs, and returns the resulting templates and
    assignments.
    """
    config = TemplateMinerConfig()
    config.drain_depth = 4
    config.drain_sim_th = 0.5
    
    # Configure masking. Order matters: specific patterns first.
    # We MUST use MaskingInstruction objects, not dicts.
    config.masking_instructions = [
        MaskingInstruction(_UUID, "<UUID>"),
        MaskingInstruction(_HEX, "<HEX>"),
        MaskingInstruction(_IP, "<IP>"),
        MaskingInstruction(_NUM, "<NUM>"),
    ]

    miner = TemplateMiner(config=config)
    assignments: dict[int, str] = {}
    line_ids_by_template: dict[str, list[int]] = {}

    for line in lines:
        # Drain3 parses the message and masks variables automatically
        res = miner.add_log_message(line.message)
        if not isinstance(res, dict):
            continue
            
        template_id = str(res.get("cluster_id", ""))
        if not template_id:
            continue
            
        assignments[line.line_id] = template_id
        line_ids_by_template.setdefault(template_id, []).append(line.line_id)

    templates: list[Template] = []
    for cluster in miner.drain.clusters:
        t_id = str(cluster.cluster_id)
        if t_id not in line_ids_by_template:
            continue
            
        ids = line_ids_by_template[t_id]
        templates.append(Template(
            template_id=t_id,
            pattern=cluster.get_template(),
            count=cluster.size,
            first_occurrence_line_id=min(ids),
            last_occurrence_line_id=max(ids),
        ))

    # Sort by integer ID to ensure stable output order
    templates.sort(key=lambda t: int(t.template_id))
    
    return templates, assignments