"""导师签认领域服务：学期目标阶段成果的受控签认入口。

签认资格凭"当期有效结对"发出：指导教师必须是责任教师在目标学期
的在任导师（结对记录状态为进行中/待验收，且结对学期与目标学期一致）。
签认结论写入学期目标待办；结对改派时在途签认转交新任导师接任，
同一目标同一阶段成果只允许存在一张正式签认。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store

ACTIVE_PAIR_STATUSES = ("进行中", "待验收")
SIGNABLE_GOAL_STATUSES = ("草稿", "待审定")


def normalize_goal_ref(raw: Any) -> str:
    """把 G-16 / g-16 / 16 / semester_goal:16 统一成 G-16；无法解析返回空串。"""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.lower().startswith("semester_goal:"):
        text = text.split(":", 1)[1].strip()
    if text.upper().startswith("G-"):
        text = text[2:].strip()
    return f"G-{int(text)}" if text.isdigit() else ""


def ref_entry_id(ref: Any, prefix: str) -> int | None:
    """从 G-16 / P-4 这类引用里解析数字编号。"""
    text = str(ref or "").strip()
    if text.upper().startswith(f"{prefix}-"):
        text = text[len(prefix) + 1:]
    return int(text) if text.isdigit() else None


def find_active_pairing(mentor: str, mentee: str, semester: str) -> dict[str, Any] | None:
    """当期有效结对：导师、青年教师、结对学期都匹配且结对仍在进行中/待验收。"""
    for row in store.rows("mentor_pair"):
        if (str(row.get("指导教师", "")).strip() == mentor
                and str(row.get("青年教师", "")).strip() == mentee
                and str(row.get("结对学期", "")).strip() == semester
                and row.get("status") in ACTIVE_PAIR_STATUSES):
            return row
    return None


class GoalSignoffService(BaseModuleService):
    MODULE = "goal_signoff"
    ENTITY = "签认请求"
    OPERATION_TERM = "提交签认"
    LIST_FIELDS = ["目标编号", "目标学期", "指导教师", "青年教师", "结对编号", "阶段成果", "签认结论", "签认人", "备注说明"]
    REQUIRED_FIELDS = ["目标编号", "指导教师", "阶段成果"]
    STATUS_ORDER = ["待提交", "待接任", "已签认", "已失效"]
    ACTION_RULES = {"提交签认": "已签认", "接任签认": "待提交", "拒接签认": "已失效", "作废签认": "已失效"}
    NEGATIVE_ACTIONS = ["拒接签认", "作废签认"]

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        viewer: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """同教研室老师带视角查询时，只能看到自己的指导事项（含待自己接任的）。"""
        rows, total = super().list_entries(
            filters=filters, keyword=keyword, status=status, page=page, size=size)
        if viewer:
            rows = [
                row for row in rows
                if str(row.get("指导教师", "")).strip() == viewer
                or str(row.get("待接任教师", "")).strip() == viewer
            ]
            total = len(rows)
        return rows, total

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """发起签认：凭当期有效结对发出资格，学期/青年教师/结对编号由系统带出。"""
        missing = [
            field for field in self.REQUIRED_FIELDS
            if not str(values.get(field) or "").strip()
        ]
        if missing:
            return None, missing

        goal_ref = normalize_goal_ref(values.get("目标编号"))
        if not goal_ref:
            raise ValueError("目标编号格式应为 G-<编号>，例如 G-16")
        goal = store.find("semester_goal", ref_entry_id(goal_ref, "G") or -1)
        if goal is None:
            raise ValueError(f"签认失败：学期目标 {goal_ref} 不存在")
        if goal.get("status") not in SIGNABLE_GOAL_STATUSES:
            raise ValueError(
                f"签认失败：学期目标 {goal_ref} 当前状态为'{goal.get('status')}'，"
                f"已审定或已废止的目标不再受理签认"
            )

        mentor = str(values.get("指导教师", "") or "").strip()
        mentee = str(goal.get("责任教师", "") or "").strip()
        semester = str(goal.get("起始学期", "") or "").strip()
        if mentor == mentee:
            raise ValueError("指导教师不能为自己的学期目标签认")

        pair = find_active_pairing(mentor, mentee, semester)
        if pair is None:
            raise ValueError(
                f"无当期有效结对：{mentor} 不是 {mentee} 在 {semester} 学期的在任导师，"
                f"不能凭其他学期的结对为该项目标签认"
            )

        stage = str(values.get("阶段成果", "") or "").strip()
        for row in store.rows(self.MODULE):
            if (str(row.get("目标编号", "")) == goal_ref
                    and str(row.get("阶段成果", "")).strip() == stage
                    and row.get("status") in ("待提交", "待接任", "已签认")):
                raise ValueError(
                    f"签认冲突：{goal_ref} 的「{stage}」已存在状态为'{row.get('status')}'的签认请求 "
                    f"#{row.get('id')}，同一阶段成果不允许重复发起"
                )

        enriched = dict(values)
        enriched.update({
            "目标编号": goal_ref,
            "目标学期": semester,
            "指导教师": mentor,
            "青年教师": mentee,
            "结对编号": f"P-{pair['id']}",
            "签认人": "",
        })
        entry, _ = super().create_entry(enriched)
        assert entry is not None
        for field in self.LIST_FIELDS:
            if entry.get(field) is None:
                entry[field] = ""
        entry["待接任教师"] = ""
        return entry, []

    def run_action(self, entry_id: int, action: str, remark: str | None = None) -> tuple[dict[str, Any] | None, str]:
        """签认动作：提交受结对资格与唯一性约束，改派后在途签认走接任流程。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        if action == "提交签认":
            return self._submit(entry, remark)
        if action == "接任签认":
            return self._take_over(entry)
        if action == "拒接签认":
            if entry.get("status") != "待接任":
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不在待接任名单内"
            self._append_note(entry, f"新任导师 {entry.get('待接任教师', '')} 拒接，签认失效")
            entry["待接任教师"] = ""
            return super().run_action(entry_id, action)
        if action == "作废签认":
            if entry.get("status") in ("已签认", "已失效"):
                return None, f"{self.ENTITY} {entry_id} 已经{entry.get('status')}，不可作废"
            return super().run_action(entry_id, action)

        return super().run_action(entry_id, action)

    def _submit(self, entry: dict[str, Any], remark: str | None) -> tuple[dict[str, Any] | None, str]:
        """提交正式签认：资格复核 + 一目标一阶段一张正式签认 + 结论写入目标待办。"""
        entry_id = int(entry.get("id", 0))
        status = entry.get("status")
        if status == "待接任":
            return None, (
                f"{self.ENTITY} {entry_id} 已随结对改派转交 {entry.get('待接任教师', '')} 接任，"
                f"不再由你签认；如需确认请查看该请求的接任状态"
            )
        if status != "待提交":
            return None, f"{self.ENTITY} {entry_id} 当前状态为'{status}'，不可提交签认"

        goal_ref = str(entry.get("目标编号", ""))
        goal = store.find("semester_goal", ref_entry_id(goal_ref, "G") or -1)
        if goal is None:
            return None, f"{self.ENTITY} {entry_id} 关联的学期目标 {goal_ref} 不存在"
        if goal.get("status") not in SIGNABLE_GOAL_STATUSES:
            return None, (
                f"学期目标 {goal_ref} 当前状态为'{goal.get('status')}'，"
                f"已审定或已废止的目标不再受理签认"
            )

        mentor = str(entry.get("指导教师", "")).strip()
        mentee = str(entry.get("青年教师", "")).strip()
        semester = str(entry.get("目标学期", "")).strip()
        pair = find_active_pairing(mentor, mentee, semester)
        if pair is None:
            return None, (
                f"签认资格失效：{mentor} 与 {mentee} 在 {semester} 学期的结对已不在有效期"
                f"（可能已改派、验收或终止），该签认不能再提交"
            )

        stage = str(entry.get("阶段成果", "")).strip()
        for row in store.rows(self.MODULE):
            if (int(row.get("id", 0)) != entry_id
                    and str(row.get("目标编号", "")) == goal_ref
                    and str(row.get("阶段成果", "")).strip() == stage
                    and row.get("status") == "已签认"):
                return None, (
                    f"签认冲突：{goal_ref} 的「{stage}」已存在正式签认 #{row.get('id')}"
                    f"（签认人 {row.get('签认人', '')}），同一阶段成果只能有一张正式签认"
                )

        conclusion = str(entry.get("签认结论", "") or "").strip()
        if not conclusion and remark:
            entry["签认结论"] = remark
            conclusion = remark
        if not conclusion:
            return None, f"{self.ENTITY} {entry_id} 尚未填写签认结论，请在登记或提交时补充阶段意见"

        result, message = super().run_action(entry_id, "提交签认")
        if result is None:
            return None, message
        # 正式签认落档：记录实际签认人与当期结对，供日后重新打开核对
        result["签认人"] = mentor
        result["结对编号"] = f"P-{pair['id']}"
        result["pending"] = False
        # 结论写入学期目标待办，主任审定前可见
        note = f"导师签认[{semester}] {mentor}：{conclusion}（结对P-{pair['id']}，签认#{entry_id}）"
        existing = str(goal.get("备注说明", "") or "").strip()
        goal["备注说明"] = f"{existing}｜{note}" if existing else note
        goal["pending"] = True
        return result, message

    def _take_over(self, entry: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """新任导师从接任名单接过在途签认：换签认人、换结对，回到待提交。"""
        entry_id = int(entry.get("id", 0))
        if entry.get("status") != "待接任":
            return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不在待接任名单内"
        new_mentor = str(entry.get("待接任教师", "")).strip()
        semester = str(entry.get("目标学期", "")).strip()
        mentee = str(entry.get("青年教师", "")).strip()
        pair = find_active_pairing(new_mentor, mentee, semester)
        if pair is None:
            return None, (
                f"接任失败：{new_mentor} 与 {mentee} 在 {semester} 学期没有有效结对，"
                f"请先在师徒结对完成改派"
            )
        entry["指导教师"] = new_mentor
        entry["待接任教师"] = ""
        entry["结对编号"] = f"P-{pair['id']}"
        self._append_note(entry, f"{new_mentor} 已接任该签认（结对P-{pair['id']}）")
        return super().run_action(entry_id, "接任签认")

    @staticmethod
    def _append_note(entry: dict[str, Any], note: str) -> None:
        existing = str(entry.get("备注说明", "") or "").strip()
        entry["备注说明"] = f"{existing}｜{note}" if existing else note
