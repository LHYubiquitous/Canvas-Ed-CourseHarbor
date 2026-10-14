"""Side-effect-free setup planning and explicit atomic commit."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .config import (
    AppConfig,
    CanvasPlatformConfig,
    ContentSelection,
    CourseConfig,
    CourseSource,
    DownloadPolicy,
    EdPlatformConfig,
    PlatformsConfig,
    save_config,
)
from .course_mapping import (
    CourseMappingChoice,
    MappingInitialSelection,
    build_mapping_plan,
)
from .providers.base import CourseCandidate


CatalogLoader = Callable[[], tuple[CourseCandidate, ...]]


@dataclass(frozen=True, slots=True)
class CourseChoice:
    """One explicit course decision; confirmation defaults to no."""

    confirmed: bool = False
    folder: str | None = None
    content: ContentSelection = field(default_factory=ContentSelection)
    policy: DownloadPolicy = field(default_factory=DownloadPolicy)


@dataclass(frozen=True, slots=True)
class SetupRequest:
    profile: str
    output_root: Path
    canvas: CanvasPlatformConfig = field(default_factory=CanvasPlatformConfig)
    ed: EdPlatformConfig = field(default_factory=EdPlatformConfig)
    choices: Mapping[str, CourseChoice] = field(default_factory=dict)
    mappings: tuple[CourseMappingChoice, ...] = ()


@dataclass(frozen=True, slots=True)
class SetupPlan:
    config: AppConfig
    preview: str
    initial_sync: tuple[MappingInitialSelection, ...] = ()


def discover_courses(
    loaders: Mapping[str, CatalogLoader], enabled_providers: set[str]
) -> dict[str, tuple[CourseCandidate, ...]]:
    """Invoke only explicitly enabled provider catalogs."""
    unknown = enabled_providers.difference(loaders)
    if unknown:
        raise ValueError(f"no catalog loader for: {', '.join(sorted(unknown))}")
    return {provider: loaders[provider]() for provider in sorted(enabled_providers)}


def _default_folder(candidate: CourseCandidate) -> str:
    return candidate.code.strip() or f"{candidate.provider}-{candidate.course_id}"


def build_setup_plan(
    request: SetupRequest,
    catalogs: Mapping[str, tuple[CourseCandidate, ...]],
) -> SetupPlan:
    """Build and validate a plan without creating files or directories."""
    if request.choices and request.mappings:
        raise ValueError("use either one-source choices or explicit course mappings")
    candidates: dict[str, CourseCandidate] = {}
    for provider_courses in catalogs.values():
        for candidate in provider_courses:
            if candidate.key in candidates:
                raise ValueError("discovered catalogs contain a duplicate course identity")
            candidates[candidate.key] = candidate

    if request.mappings:
        mapping_plan = build_mapping_plan(catalogs, request.mappings)
        config = AppConfig(
            profile=request.profile,
            output_root=request.output_root,
            platforms=PlatformsConfig(canvas=request.canvas, ed=request.ed),
            courses=mapping_plan.courses,
        )
        preview = "\n".join(
            (
                f"Profile: {request.profile}",
                f"Output root: {request.output_root}",
                mapping_plan.preview,
            )
        )
        return SetupPlan(config, preview, mapping_plan.initial_sync)

    selected: list[CourseConfig] = []
    preview_lines = [
        f"Profile: {request.profile}",
        f"Output root: {request.output_root}",
        "Selected courses:",
    ]
    for key, choice in request.choices.items():
        if not choice.confirmed:
            continue
        candidate = candidates.get(key)
        if candidate is None:
            raise ValueError(f"confirmed course is not in the discovered catalog: {key}")
        folder = choice.folder or _default_folder(candidate)
        source = CourseSource(
            provider=candidate.provider,  # type: ignore[arg-type]
            instance=candidate.instance,
            course_id=candidate.course_id,
            code=candidate.code or None,
            name=candidate.name or None,
            term=candidate.term,
        )
        selected.append(
            CourseConfig(
                key=candidate.key,
                folder=folder,
                confirmed_at=datetime.now(timezone.utc),
                sources=(source,),
                content=choice.content,
                policy=choice.policy,
            )
        )
        preview_lines.append(
            f"- {candidate.provider} id={candidate.course_id} code={candidate.code or '-'} "
            f"term={candidate.term or '-'} -> {request.output_root / folder}"
        )
    if not selected:
        preview_lines.append("- none (course confirmation defaults to no)")
    config = AppConfig(
        profile=request.profile,
        output_root=request.output_root,
        platforms=PlatformsConfig(canvas=request.canvas, ed=request.ed),
        courses=tuple(selected),
    )
    return SetupPlan(config=config, preview="\n".join(preview_lines))


def commit_setup(plan: SetupPlan, path: Path, *, confirmed: bool) -> bool:
    """Commit only after the caller's explicit final confirmation."""
    if not confirmed:
        return False
    save_config(plan.config, path)
    return True


def _yes(value: str) -> bool:
    return value.strip().casefold() in {"y", "yes"}


def run_setup_wizard(
    *,
    profile: str,
    config_path: Path,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    secret_resolver: Callable[[str], str],
    canvas_catalog_loader: Callable[[str, str], tuple[CourseCandidate, ...]],
    default_output_root: Path,
    dry_run: bool = False,
) -> bool:
    """Run the Canvas Phase 1 wizard; every provider/course/final choice is opt-in."""
    provider = input_fn("Provider to enable [canvas; blank cancels]: ").strip().casefold()
    if not provider:
        output_fn("Setup cancelled before provider selection; no changes made.")
        return False
    if provider != "canvas":
        raise ValueError("Phase 1 setup supports Canvas only; Ed arrives in Phase 3")

    base_url = input_fn(
        "Canvas base URL [https://canvas.sydney.edu.au/api/v1]: "
    ).strip() or "https://canvas.sydney.edu.au/api/v1"
    credential = input_fn(
        f"Credential reference [keyring:canvas-ed-mcp/{profile}/canvas]: "
    ).strip() or f"keyring:canvas-ed-mcp/{profile}/canvas"
    output_text = input_fn(f"Output root [{default_output_root}]: ").strip()
    output_root = Path(output_text).expanduser().resolve() if output_text else default_output_root

    token = secret_resolver(credential)
    catalog = canvas_catalog_loader(base_url, token)
    choices: dict[str, CourseChoice] = {}
    for candidate in catalog:
        label = (
            f"{candidate.name} | code={candidate.code or '-'} | "
            f"term={candidate.term or '-'} | id={candidate.course_id}"
        )
        if not _yes(input_fn(f"Select {label}? [y/N]: ")):
            continue
        default_folder = _default_folder(candidate)
        folder = input_fn(f"Folder [{default_folder}]: ").strip() or default_folder
        categories = input_fn("Content [files,pages]: ").strip().casefold() or "files,pages"
        selected_categories = {item.strip() for item in categories.split(",") if item.strip()}
        allowed_categories = {"files", "pages"}
        if not selected_categories.issubset(allowed_categories):
            raise ValueError("Canvas Phase 1 content choices are files and pages")
        max_mib_text = input_fn("Maximum single-file MiB [100]: ").strip()
        max_mib = int(max_mib_text) if max_mib_text else 100
        if max_mib <= 0:
            raise ValueError("maximum file size must be positive")
        choices[candidate.key] = CourseChoice(
            confirmed=True,
            folder=folder,
            content=ContentSelection(
                files="files" in selected_categories,
                pages="pages" in selected_categories,
                lessons=False,
                resources=False,
            ),
            policy=DownloadPolicy(max_file_bytes=max_mib * 1024 * 1024),
        )

    request = SetupRequest(
        profile=profile,
        output_root=output_root,
        canvas=CanvasPlatformConfig(
            enabled=True,
            base_url=base_url,
            credential=credential,
        ),
        choices=choices,
    )
    plan = build_setup_plan(request, {"canvas": catalog})
    output_fn(plan.preview)
    if dry_run:
        output_fn("Dry-run only; no config, state, or course content was written.")
        return False
    confirmed = _yes(input_fn("Commit this configuration? [y/N]: "))
    committed = commit_setup(plan, config_path, confirmed=confirmed)
    output_fn(
        f"Configuration saved to {config_path}"
        if committed
        else "Setup cancelled at final confirmation; no changes made."
    )
    return committed
