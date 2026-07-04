"""Layout/diagram settings loaded from .env via ConfigPort."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.adapters.outbound.config.env_config import get_config


@dataclass(frozen=True)
class LayoutSettings:
    leaf_w: int
    leaf_h: int
    leaf_cell_w: int
    leaf_gap: int
    container_pad: int
    container_pad_top: int
    container_gap: int
    root_top: int
    root_left: int
    max_cols: int
    default_container_w: int
    default_container_h: int
    tier_column_w: int
    az_column_w: int
    default_page_w: int
    default_page_h: int
    page_margin: int
    face_slots: tuple[float, ...]
    edge_color_aws: str
    edge_color_gcp: str
    edge_color_azure: str
    igw_vpc_y: int
    igw_min_x_pad: int
    label_band_height: int
    min_inner_pad: int
    badge_size: int
    badge_offset: int
    flow_notes_x: int
    flow_notes_gap: int
    flow_notes_min_w: int
    flow_notes_max_w: int
    flow_notes_row_h: int
    band_gap: int
    band_pad: int
    band_min_h: int
    scaffold_cloud: tuple[int, int]
    scaffold_vpc: tuple[int, int]
    scaffold_az: tuple[int, int]
    scaffold_subnet: tuple[int, int]
    keep_empty_bands_default: bool
    quality_gates_blocking: bool


@lru_cache(maxsize=1)
def layout_settings() -> LayoutSettings:
    c = get_config()
    return LayoutSettings(
        leaf_w=c.leaf_node_width,
        leaf_h=c.leaf_node_height,
        leaf_cell_w=c.leaf_cell_width,
        leaf_gap=c.leaf_gap,
        container_pad=c.container_pad,
        container_pad_top=c.container_pad_top,
        container_gap=c.container_gap,
        root_top=c.root_top,
        root_left=c.get_int("ROOT_LEFT", 40),
        max_cols=c.max_cols,
        default_container_w=c.default_container_w,
        default_container_h=c.default_container_h,
        tier_column_w=c.tier_column_w,
        az_column_w=c.az_column_w,
        default_page_w=c.default_page_w,
        default_page_h=c.default_page_h,
        page_margin=c.get_int("PAGE_MARGIN", 20),
        face_slots=c.face_slots,
        edge_color_aws=c.edge_color_aws,
        edge_color_gcp=c.edge_color_gcp,
        edge_color_azure=c.edge_color_azure,
        igw_vpc_y=c.get_int("IGW_VPC_Y", -30),
        igw_min_x_pad=c.get_int("IGW_MIN_X_PAD", 28),
        label_band_height=c.get_int("LABEL_BAND_HEIGHT", 40),
        min_inner_pad=c.get_int("MIN_INNER_PAD", 20),
        badge_size=c.get_int("BADGE_SIZE", 22),
        badge_offset=c.get_int("BADGE_OFFSET", -6),
        flow_notes_x=c.get_int("FLOW_NOTES_X", 40),
        flow_notes_gap=c.get_int("FLOW_NOTES_GAP", 48),
        flow_notes_min_w=c.get_int("FLOW_NOTES_MIN_W", 520),
        flow_notes_max_w=c.get_int("FLOW_NOTES_MAX_W", 760),
        flow_notes_row_h=c.get_int("FLOW_NOTES_ROW_H", 30),
        band_gap=c.get_int("BAND_GAP", 24),
        band_pad=c.get_int("BAND_PAD", 20),
        band_min_h=c.get_int("BAND_MIN_H", 88),
        scaffold_cloud=(c.get_int("SCAFFOLD_CLOUD_W", 920), c.get_int("SCAFFOLD_CLOUD_H", 540)),
        scaffold_vpc=(c.get_int("SCAFFOLD_VPC_W", 980), c.get_int("SCAFFOLD_VPC_H", 420)),
        scaffold_az=(c.get_int("SCAFFOLD_AZ_W", 320), c.get_int("SCAFFOLD_AZ_H", 380)),
        scaffold_subnet=(c.get_int("SCAFFOLD_SUBNET_W", 300), c.get_int("SCAFFOLD_SUBNET_H", 260)),
        keep_empty_bands_default=c.get_bool("KEEP_EMPTY_BANDS_DEFAULT", True),
        quality_gates_blocking=c.get_bool("QUALITY_GATES_BLOCKING", True),
    )


def platform_meta(platform: str) -> tuple[str, str, str]:
    key = (platform or "aws").lower()
    labels = {
        "aws": ("Amazon Web Services", layout_settings().edge_color_aws),
        "gcp": ("Google Cloud Platform", layout_settings().edge_color_gcp),
        "azure": ("Microsoft Azure", layout_settings().edge_color_azure),
        "multi": ("Multi-Cloud / Hybrid", "#8C4FFF"),
    }
    label, accent = labels.get(key, labels["aws"])
    return key, label, accent
