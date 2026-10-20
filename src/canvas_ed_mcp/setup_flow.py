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
    save_new_config,
)
from .course_mapping import (
    CourseMappingChoice,
    MappingInitialSelection,
    MappingSourceChoice,
    build_mapping_plan,
)
from .initial import InitialSyncMode
from .providers.base import CourseCandidate
from .course_mapping import SourceIdentity


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


class SetupConflictError(RuntimeError):
    """The target profile already exists and must be changed through configure."""


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


def commit_setup(
    plan: SetupPlan,
    path: Path,
    *,
    confirmed: bool,
    warning_fn: Callable[[str], None] | None = None,
) -> bool:
    """Commit only after the caller's explicit final confirmation."""
    if not confirmed:
        return False
    try:
        cleanup_pending = save_new_config(plan.config, path)
    except FileExistsError as exc:
        raise SetupConflictError(
            "configuration already exists; use configure instead of setup"
        ) from exc
    if cleanup_pending and warning_fn is not None:
        warning_fn("setup warning (config_cleanup_pending)")
    return True


def _yes(value: str) -> bool:
    return value.strip().casefold() in {"y", "yes"}


def _initial_mode(value: str) -> InitialSyncMode:
    normalized = value.strip().casefold().replace("-", "_")
    if not normalized or normalized in {"baseline", "baseline_from_now"}:
        return InitialSyncMode.BASELINE_FROM_NOW
    if normalized in {"import", "import_all"}:
        return InitialSyncMode.IMPORT_ALL
    raise ValueError("initial mode must be baseline_from_now or import_all")


def _content_selection(value: str, allowed: set[str]) -> ContentSelection:
    selected = {item.strip().casefold() for item in value.split(",") if item.strip()}
    if not selected.issubset(allowed):
        raise ValueError(f"content choices must be: {', '.join(sorted(allowed))}")
    return ContentSelection(
        files="files" in selected,
        pages="pages" in selected,
        lessons="lessons" in selected,
        resources="resources" in selected,
    )


def _validate_catalog_candidate(candidate: CourseCandidate, provider: str) -> None:
    if not isinstance(candidate, CourseCandidate) or candidate.provider != provider:
        raise ValueError("catalog candidate does not match its provider")
    SourceIdentity.from_candidate(candidate)
    for value in (candidate.code, candidate.name, candidate.term):
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value
        ):
            raise ValueError("catalog display text contains control characters")


def run_setup_wizard(
    *,
    profile: str,
    config_path: Path,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    secret_resolver: Callable[[str], str],
    canvas_catalog_loader: Callable[[str, str], tuple[CourseCandidate, ...]],
    ed_catalog_loader: Callable[[str], tuple[CourseCandidate, ...]] | None = None,
    default_output_root: Path,
    dry_run: bool = False,
) -> bool:
    """Run the interactive setup wizard; every provider/course/final choice is opt-in."""
    if config_path.exists():
        raise SetupConflictError(
            "configuration already exists; use configure instead of setup"
        )
    provider = input_fn(
        "Provider(s) to enable [canvas/ed/both; blank cancels]: "
    ).strip().casefold()
    if not provider:
        output_fn("Setup cancelled before provider selection; no changes made.")
        return False
    if provider not in {"canvas", "ed", "both"}:
        raise ValueError("provider selection must be canvas, ed, or both")

    # Keep the established Canvas-only interaction stable for existing profiles.
    if provider == "canvas":
        return _run_canvas_only_setup(
            profile=profile,
            config_path=config_path,
            input_fn=input_fn,
            output_fn=output_fn,
            secret_resolver=secret_resolver,
            canvas_catalog_loader=canvas_catalog_loader,
            default_output_root=default_output_root,
            dry_run=dry_run,
        )

    canvas = CanvasPlatformConfig()
    ed = EdPlatformConfig()
    catalogs: dict[str, tuple[CourseCandidate, ...]] = {}
    if provider == "both":
        base_url = input_fn(
            "Canvas base URL [https://canvas.sydney.edu.au/api/v1]: "
        ).strip() or "https://canvas.sydney.edu.au/api/v1"
        credential = input_fn(
            f"Canvas credential reference [keyring:canvas-ed-mcp/{profile}/canvas]: "
        ).strip() or f"keyring:canvas-ed-mcp/{profile}/canvas"
        canvas = CanvasPlatformConfig(
            enabled=True, base_url=base_url, credential=credential
        )
        catalogs["canvas"] = canvas_catalog_loader(
            base_url, secret_resolver(credential)
        )

    region = input_fn("Ed region [global]: ").strip().casefold() or "global"
    if region != "global":
        raise ValueError("only the global Ed region is currently supported")
    ed_credential = input_fn(
        f"Ed credential reference [keyring:canvas-ed-mcp/{profile}/ed]: "
    ).strip() or f"keyring:canvas-ed-mcp/{profile}/ed"
    if ed_catalog_loader is None:
        raise ValueError("Ed catalog loader is unavailable")
    ed = EdPlatformConfig(enabled=True, region=region, credential=ed_credential)
    catalogs["ed"] = ed_catalog_loader(secret_resolver(ed_credential))

    output_text = input_fn(f"Output root [{default_output_root}]: ").strip()
    output_root = (
        Path(output_text).expanduser().resolve() if output_text else default_output_root
    )

    grouped: dict[str, list[tuple[CourseCandidate, InitialSyncMode]]] = {}
    for provider_name in ("canvas", "ed"):
        for candidate in catalogs.get(provider_name, ()):
            _validate_catalog_candidate(candidate, provider_name)
            label = (
                f"{candidate.name} | provider={candidate.provider} | "
                f"instance={candidate.instance} | code={candidate.code or '-'} | "
                f"term={candidate.term or '-'} | id={candidate.course_id}"
            )
            if not _yes(input_fn(f"Select {label}? [y/N]: ")):
                continue
            mode = _initial_mode(
                input_fn(
                    "Initial mode [baseline_from_now/import_all; "
                    "default baseline_from_now]: "
                )
            )
            logical_key = input_fn(
                f"Logical course key [{candidate.key}]: "
            ).strip() or candidate.key
            grouped.setdefault(logical_key, []).append((candidate, mode))

    mappings: list[CourseMappingChoice] = []
    for logical_key, selections in grouped.items():
        identities = ", ".join(
            f"{candidate.provider}@{candidate.instance} id={candidate.course_id}"
            for candidate, _ in selections
        )
        if not _yes(input_fn(f"Confirm mapping {logical_key}: {identities}? [y/N]: ")):
            continue
        default_folder = _default_folder(selections[0][0])
        folder = input_fn(f"Folder [{default_folder}]: ").strip() or default_folder
        present = {candidate.provider for candidate, _ in selections}
        allowed: set[str] = set()
        if "canvas" in present:
            allowed.update(("files", "pages"))
        if "ed" in present:
            allowed.update(("lessons", "resources"))
        default_content = ",".join(
            item for item in ("files", "pages", "lessons", "resources") if item in allowed
        )
        categories = input_fn(f"Content [{default_content}]: ").strip() or default_content
        max_mib_text = input_fn("Maximum single-file MiB [100]: ").strip()
        max_mib = int(max_mib_text) if max_mib_text else 100
        if max_mib <= 0:
            raise ValueError("maximum file size must be positive")
        mappings.append(
            CourseMappingChoice(
                course_key=logical_key,
                sources=tuple(
                    MappingSourceChoice(
                        candidate.key, confirmed=True, initial_mode=mode
                    )
                    for candidate, mode in selections
                ),
                confirmed=True,
                folder=folder,
                content=_content_selection(categories, allowed),
                policy=DownloadPolicy(max_file_bytes=max_mib * 1024 * 1024),
            )
        )

    request = SetupRequest(
        profile=profile,
        output_root=output_root,
        canvas=canvas,
        ed=ed,
        mappings=tuple(mappings),
    )
    plan = build_setup_plan(request, catalogs)
    output_fn(plan.preview)
    if dry_run:
        output_fn("Dry-run only; no config, state, or course content was written.")
        return False
    confirmed = _yes(input_fn("Commit this configuration? [y/N]: "))
    committed = commit_setup(
        plan, config_path, confirmed=confirmed, warning_fn=output_fn
    )
    output_fn(
        f"Configuration saved to {config_path}"
        if committed
        else "Setup cancelled at final confirmation; no changes made."
    )
    return committed


def _run_canvas_only_setup(
    *,
    profile: str,
    config_path: Path,
    input_fn: Callable[[str], str],
    output_fn: Callable[[str], None],
    secret_resolver: Callable[[str], str],
    canvas_catalog_loader: Callable[[str, str], tuple[CourseCandidate, ...]],
    default_output_root: Path,
    dry_run: bool,
) -> bool:
    """Preserve the reviewed Canvas-only setup interaction."""

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
        _validate_catalog_candidate(candidate, "canvas")
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
    committed = commit_setup(
        plan, config_path, confirmed=confirmed, warning_fn=output_fn
    )
    output_fn(
        f"Configuration saved to {config_path}"
        if committed
        else "Setup cancelled at final confirmation; no changes made."
    )
    return committed
