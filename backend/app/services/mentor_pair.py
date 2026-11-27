"""师徒结对领域服务：结对登记、验收、改派、结项，含跨模块教师冲突检测和学期目标关联。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store

ACTIVE_PAIR_STATUSES = ("进行中", "待验收")


class MentorPairService(BaseModuleService):
    MODULE = "mentor_pair"
    ENTITY = "结对记录"
    OPERATION_TERM = "验收结对"
    LIST_FIELDS = ["指导教师", "青年教师", "结对目标", "起止日期", "结对学期", "指导计划", "阶段产出", "终期评价", "备注说明"]
    REQUIRED_FIELDS = ["指导教师", "青年教师", "结对目标"]
    STATUS_ORDER = ["草稿", "进行中", "待验收", "已结项"]
    ACTION_RULES = {"验收结对": "已结项", "调整计划": "进行中", "终止结对": "已结项", "改派导师": "已结项"}
    NEGATIVE_ACTIONS = []

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """创建结对记录：同一指导教师不能同时被多个结对占用同一时段。"""
        mentor = str(values.get("指导教师", "") or "").strip()
        date_range = str(values.get("起止日期", "") or "").strip()
        teacher = str(values.get("青年教师", "") or "").strip()

        if mentor and teacher and mentor == teacher:
            raise ValueError("指导教师和青年教师不能是同一个人")

        conflict = self._find_active_conflict(mentor, date_range)
        if conflict is not None:
            raise ValueError(
                f"结对冲突：指导教师 {mentor} 在 {date_range} 时段已有进行中的结对，"
                f"不能同时被多个结对占用"
            )

        return super().create_entry(values)

    @staticmethod
    def _find_active_conflict(mentor: str, date_range: str) -> dict[str, Any] | None:
        if not mentor or not date_range:
            return None
        for row in store.rows("mentor_pair"):
            if (str(row.get("指导教师", "")).strip() == mentor
                    and str(row.get("起止日期", "")).strip() == date_range
                    and row.get("status") in ACTIVE_PAIR_STATUSES):
                return row
        return None

    def run_action(self, entry_id: int, action: str, remark: str | None = None) -> tuple[dict[str, Any] | None, str]:
        """执行动作，含业务约束和跨模块关联。"""
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"

        # 验收结对：必须在进行中或待验收状态
        if action == "验收结对":
            if entry.get("status") not in ("进行中", "待验收"):
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不可验收"

        # 终止结对：必须在进行中或待验收状态
        if action == "终止结对":
            if entry.get("status") not in ("进行中", "待验收"):
                return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不可终止"

        if action == "改派导师":
            return self._reassign(entry, remark)

        result, message = super().run_action(entry_id, action)
        # 结对验收/终止后，凭该结对发出的在途签认一并失效（资格随结对结束而失效）
        if result is not None and action in ("验收结对", "终止结对"):
            self._expire_inflight_signoffs(entry, action)
        return result, message

    def _reassign(self, entry: dict[str, Any], remark: str | None) -> tuple[dict[str, Any] | None, str]:
        """改派导师：原结对结项，新导师结对接任；在途签认转交新导师，正式签认不受影响。"""
        entry_id = int(entry.get("id", 0))
        if entry.get("status") not in ACTIVE_PAIR_STATUSES:
            return None, f"{self.ENTITY} {entry_id} 当前状态为'{entry.get('status')}'，不可改派导师"

        new_mentor = str(remark or "").strip()
        if not new_mentor:
            return None, "改派导师需要在备注中写明新任指导教师"
        old_mentor = str(entry.get("指导教师", "")).strip()
        mentee = str(entry.get("青年教师", "")).strip()
        if new_mentor == old_mentor:
            return None, f"新任指导教师 {new_mentor} 与原导师相同，无需改派"
        if new_mentor == mentee:
            return None, "新任指导教师不能是青年教师本人"

        date_range = str(entry.get("起止日期", "") or "").strip()
        conflict = self._find_active_conflict(new_mentor, date_range)
        if conflict is not None:
            return None, (
                f"结对冲突：新任指导教师 {new_mentor} 在 {date_range} 时段已有进行中的结对，"
                f"不能接任该结对"
            )

        # 新结对接任：沿用原结对的目标、时段与学期
        rows = store.rows(self.MODULE)
        new_pair = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "指导教师": new_mentor,
            "青年教师": mentee,
            "结对目标": entry.get("结对目标", ""),
            "起止日期": date_range,
            "结对学期": entry.get("结对学期", ""),
            "指导计划": entry.get("指导计划", ""),
            "阶段产出": entry.get("阶段产出", ""),
            "终期评价": "",
            "备注说明": f"由结对P-{entry_id}改派（原导师 {old_mentor}）",
            "status": "进行中",
            "pending": True,
            "abnormal": False,
        }
        rows.append(new_pair)

        # 在途签认（待提交）转交新导师接任；已签认的正式签认保持原样，历史可追溯
        transferred = 0
        for req in store.rows("goal_signoff"):
            if (str(req.get("结对编号", "")).strip() == f"P-{entry_id}"
                    and req.get("status") == "待提交"):
                req["status"] = "待接任"
                req["待接任教师"] = new_mentor
                note = f"结对P-{entry_id}改派，转交 {new_mentor} 接任"
                existing = str(req.get("备注说明", "") or "").strip()
                req["备注说明"] = f"{existing}｜{note}" if existing else note
                transferred += 1

        # 原结对结项
        result, _ = super().run_action(entry_id, "改派导师")
        assert result is not None
        note = f"已改派给 {new_mentor}（新结对P-{new_pair['id']}）"
        existing = str(result.get("备注说明", "") or "").strip()
        result["备注说明"] = f"{existing}｜{note}" if existing else note
        message = (
            f"{self.ENTITY} {entry_id} 已改派：{old_mentor} → {new_mentor}，"
            f"新结对P-{new_pair['id']}已建立"
        )
        if transferred:
            message += f"，{transferred} 笔在途签认已转入 {new_mentor} 的接任名单"
        return result, message

    @staticmethod
    def _expire_inflight_signoffs(entry: dict[str, Any], action: str) -> None:
        pair_ref = f"P-{entry.get('id')}"
        for req in store.rows("goal_signoff"):
            if (str(req.get("结对编号", "")).strip() == pair_ref
                    and req.get("status") == "待提交"):
                req["status"] = "已失效"
                req["pending"] = False
                note = f"结对{action}，签认资格失效"
                existing = str(req.get("备注说明", "") or "").strip()
                req["备注说明"] = f"{existing}｜{note}" if existing else note
