from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from ..config import RightsConfig
from ..models import SourceMetadata
from ..utils.files import atomic_write_json, atomic_write_text

RIGHTS_BASIS_LABELS = {
    "unspecified": "未填写（发布前必须人工核对）",
    "owned": "本人或本团队原创",
    "written_permission": "已获得权利人书面授权",
    "cc_by": "Creative Commons CC BY",
    "cc_by_sa": "Creative Commons CC BY-SA",
    "cc_by_nc": "Creative Commons CC BY-NC（仅限非商业使用）",
    "public_domain": "公共领域",
    "other": "其他许可或版权例外",
}


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_rights(config: RightsConfig, *, strict: bool = True) -> list[str]:
    """Return actionable rights-record problems without making a legal determination."""
    issues: list[str] = []
    if config.basis == "unspecified":
        issues.append("请选择版权依据。")
    if not config.allow_translation:
        issues.append("当前授权不包含翻译或改编权。")
    if not config.allow_redistribution:
        issues.append("当前授权不包含重新发布或分发权。")
    if config.basis == "written_permission":
        if not config.rights_holder.strip():
            issues.append("书面授权需要填写权利人。")
        if not config.permission_reference.strip():
            issues.append("书面授权需要填写可核对的授权证明或日期。")
    if config.basis in {"cc_by", "cc_by_sa", "cc_by_nc"} and not _is_http_url(config.license_url):
        issues.append("Creative Commons 素材需要填写公开许可页面链接。")
    if config.basis == "public_domain" and not _is_http_url(config.license_url):
        issues.append("公共领域素材需要填写可核对的来源或权利状态链接。")
    if config.basis == "other" and not config.permission_reference.strip():
        issues.append("其他许可需要填写依据与核对说明。")
    if config.basis == "cc_by_nc" and config.commercial_use:
        issues.append("CC BY-NC 不允许将该项目标记为商业使用。")
    return issues if strict else [issue for issue in issues if config.basis != "unspecified"]


def attribution_for(metadata: SourceMetadata, config: RightsConfig) -> str:
    if config.attribution_text.strip():
        return config.attribution_text.strip()
    holder = config.rights_holder.strip() or metadata.channel.strip() or "（请填写权利人）"
    source_url = (
        metadata.source_input
        if metadata.source_type == "webpage_media"
        else metadata.source_url or metadata.source_input
    )
    lines = [f"原作品／权利人：{holder}", f"来源：{source_url}"]
    if config.basis in {"cc_by", "cc_by_sa", "cc_by_nc"}:
        lines.append(f"许可：{RIGHTS_BASIS_LABELS[config.basis]}")
        lines.append(f"许可页面：{config.license_url.strip()}")
        lines.append("改动：翻译、字幕排版及视频本地化处理。")
    elif config.basis == "public_domain":
        lines.append(f"权利状态依据：{config.license_url.strip()}")
    elif config.basis == "written_permission":
        lines.append("使用依据：已获得权利人对下载、翻译和重新发布的书面许可。")
    else:
        lines.append(f"使用依据：{RIGHTS_BASIS_LABELS[config.basis]}")
    return "\n".join(lines)


def generate_rights_assets(
    metadata: SourceMetadata,
    output_dir: Path,
    config: RightsConfig,
) -> list[Path]:
    """Write a local audit trail and ready-to-copy attribution beside publishing drafts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    source_url = (
        metadata.source_input
        if metadata.source_type == "webpage_media"
        else metadata.source_url or metadata.source_input
    )
    issues = validate_rights(config, strict=True)
    record = {
        "generated_at": datetime.now(UTC).isoformat(),
        "record_type": "user-supplied rights declaration",
        "not_legal_advice": True,
        "requires_human_review": bool(issues),
        "source": {
            "title": metadata.title,
            "channel": metadata.channel,
            "source_type": metadata.source_type,
            "source_url_or_file": source_url,
            "video_id": metadata.video_id,
        },
        "rights": config.model_dump(mode="json"),
        "basis_label": RIGHTS_BASIS_LABELS[config.basis],
        "validation_issues": issues,
    }
    json_path = output_dir / "RIGHTS_RECORD.json"
    markdown_path = output_dir / "RIGHTS_RECORD.md"
    attribution_path = output_dir / "ATTRIBUTION.txt"
    atomic_write_json(json_path, record)
    lines = [
        "# Rights record",
        "",
        "> This file records information supplied by the user. It is not legal advice or proof of ownership.",
        "",
        f"- Source: {metadata.title}",
        f"- Rights basis: {RIGHTS_BASIS_LABELS[config.basis]}",
        f"- Rights holder: {config.rights_holder or metadata.channel or 'not supplied'}",
        f"- Translation/adaptation allowed: {'yes' if config.allow_translation else 'no'}",
        f"- Redistribution allowed: {'yes' if config.allow_redistribution else 'no'}",
        f"- Commercial use declared: {'yes' if config.commercial_use else 'no'}",
        f"- License/evidence URL: {config.license_url or 'not supplied'}",
        f"- Permission reference: {config.permission_reference or 'not supplied'}",
        "",
        "## Validation",
        "",
    ]
    lines.extend(f"- {issue}" for issue in issues)
    if not issues:
        lines.append("- Required fields are complete; verify the actual license before publishing.")
    atomic_write_text(markdown_path, "\n".join(lines) + "\n")
    atomic_write_text(attribution_path, attribution_for(metadata, config) + "\n")
    return [json_path, markdown_path, attribution_path]
