from __future__ import annotations

import datetime

from hypothesis import given
from hypothesis import strategies as st
from minio.datatypes import Object

from run import collect_update_targets, is_after_s3_cursor, keep_latest_objects


def make_object(object_name: str, last_modified: datetime.datetime) -> Object:
    """PBT 入力用の MinIO Object を組み立てる。"""
    return Object("test-bucket", object_name, last_modified, "etag", 10)


def tz_aware_datetimes() -> st.SearchStrategy[datetime.datetime]:
    """タイムゾーン情報を含む datetime を生成する。"""
    return st.datetimes(
        min_value=datetime.datetime(2000, 1, 1),
        max_value=datetime.datetime(2035, 12, 31),
        timezones=st.timezones(),
    )


@st.composite
def unique_named_objects(draw: st.DrawFn) -> list[Object]:
    """object_name が一意な Object 列を生成する。

    (last_modified, object_name) の一意性は object_name の一意性で保証する。
    last_modified は少数の候補から選ぶことで、 同値グループを意図的に含める。
    """
    timestamps = draw(
        st.lists(tz_aware_datetimes(), min_size=1, max_size=8, unique=True)
    )
    names = draw(
        st.lists(
            st.text(
                alphabet=st.characters(
                    codec="ascii",
                    min_codepoint=ord("0"),
                    max_codepoint=ord("z"),
                ),
                min_size=1,
                max_size=24,
            ),
            min_size=1,
            max_size=40,
            unique=True,
        )
    )
    return [make_object(name, draw(st.sampled_from(timestamps))) for name in names]


def keep_latest_objects_naive(objects: list[Object], max_objects: int) -> list[Object]:
    """全件を (last_modified, object_name) で降順ソートし先頭 max_objects 件を返す。"""
    ordered = sorted(
        objects,
        key=lambda obj: (obj.last_modified, obj.object_name),
        reverse=True,
    )
    return ordered[:max_objects]


def collect_update_targets_naive(
    objects: list[Object],
    cursor_key: tuple[datetime.datetime, str],
    update_maximum_load: int,
) -> tuple[list[Object], list[Object]]:
    """オフラインで target / same 集合を計算する参照実装。"""
    cursor_last_modified, cursor_object_name = cursor_key
    newer = [
        obj
        for obj in objects
        if is_after_s3_cursor((obj.last_modified, obj.object_name), cursor_key)
    ]
    newer.sort(key=lambda obj: (obj.last_modified, obj.object_name))
    target_log_objects = newer[:update_maximum_load]
    same_last_modified_objects = [
        obj
        for obj in objects
        if obj.last_modified == cursor_last_modified
        and obj.object_name != cursor_object_name
    ]
    return target_log_objects, same_last_modified_objects


@given(
    objects=unique_named_objects(),
    max_objects=st.integers(min_value=1, max_value=50),
)
def test_keep_latest_objects_matches_naive(
    objects: list[Object], max_objects: int
) -> None:
    """keep_latest_objects がナイーブな降順切り詰めと一致することを確認する。"""
    actual = keep_latest_objects(iter(objects), max_objects)
    expected = keep_latest_objects_naive(objects, max_objects)
    assert actual == expected


@given(data=st.data())
def test_collect_update_targets_matches_naive(data: st.DataObject) -> None:
    """collect_update_targets がオフライン参照実装と一致することを確認する。"""
    objects = data.draw(unique_named_objects())
    cursor_obj = data.draw(st.sampled_from(objects))
    assert cursor_obj.last_modified is not None
    assert cursor_obj.object_name is not None
    cursor_key = (cursor_obj.last_modified, cursor_obj.object_name)
    update_maximum_load = data.draw(
        st.integers(min_value=1, max_value=len(objects) + 5)
    )

    actual = collect_update_targets(iter(objects), cursor_key, update_maximum_load)
    expected = collect_update_targets_naive(objects, cursor_key, update_maximum_load)
    assert actual == expected
