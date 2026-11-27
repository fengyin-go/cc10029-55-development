"""导师签认业务规则测试：受控入口、学期隔离、改派冲突、审定核对、历史追溯。"""
import pytest

from app.services.goal_signoff import GoalSignoffService
from app.services.mentor_pair import MentorPairService
from app.services.semester_goal import SemesterGoalService
from app.store import store


def clear_all():
    for name in store.module_names():
        store.rows(name).clear()


def make_goal(svc, goal_id_semester="2026-秋季", teacher="于秋", status=None, desc="提升课堂互动覆盖率"):
    goal, missing = svc.create_entry({
        "目标描述": desc,
        "量化指标": "互动环节覆盖率≥80%",
        "责任教师": teacher,
        "起始学期": goal_id_semester,
    })
    assert missing == []
    if status:
        goal["status"] = status
    return goal


def make_pair(svc, mentor="林老师", mentee="于秋", semester="2026-秋季", status="进行中",
              date_range="2026-09至2027-01"):
    pair, missing = svc.create_entry({
        "指导教师": mentor,
        "青年教师": mentee,
        "结对目标": "提升课堂互动与教学反馈能力",
        "起止日期": date_range,
        "结对学期": semester,
    })
    assert missing == []
    pair["status"] = status
    return pair


class TestControlledSignoffEntry:
    """受控的导师签认入口：签认资格凭当期有效结对发出"""

    def setup_method(self):
        clear_all()
        self.signoff_svc = GoalSignoffService()
        self.pair_svc = MentorPairService()
        self.goal_svc = SemesterGoalService()

    def test_create_signoff_with_valid_pairing(self):
        goal = make_goal(self.goal_svc, status="待审定")
        pair = make_pair(self.pair_svc)
        req, missing = self.signoff_svc.create_entry({
            "目标编号": f"G-{goal['id']}",
            "指导教师": "林老师",
            "阶段成果": "第一阶段听课记录",
            "签认结论": "阶段成果达标",
        })
        assert missing == []
        assert req["status"] == "待提交"
        # 学期、青年教师、结对编号由系统从结对与目标带出
        assert req["目标学期"] == "2026-秋季"
        assert req["青年教师"] == "于秋"
        assert req["结对编号"] == f"P-{pair['id']}"

    def test_create_signoff_without_pairing_rejected(self):
        goal = make_goal(self.goal_svc, status="待审定")
        with pytest.raises(ValueError, match="无当期有效结对"):
            self.signoff_svc.create_entry({
                "目标编号": f"G-{goal['id']}",
                "指导教师": "林老师",
                "阶段成果": "第一阶段听课记录",
            })

    def test_spring_pairing_cannot_sign_autumn_goal(self):
        """同一位老师在春季做过导师，也不能凭那份结对为秋季目标签认"""
        goal = make_goal(self.goal_svc, goal_id_semester="2026-秋季", status="待审定")
        make_pair(self.pair_svc, semester="2026-春季", date_range="2026-02至2026-07")
        with pytest.raises(ValueError, match="无当期有效结对"):
            self.signoff_svc.create_entry({
                "目标编号": f"G-{goal['id']}",
                "指导教师": "林老师",
                "阶段成果": "第一阶段听课记录",
            })

    def test_closed_pairing_cannot_sign(self):
        goal = make_goal(self.goal_svc, status="待审定")
        make_pair(self.pair_svc, status="已结项")
        with pytest.raises(ValueError, match="无当期有效结对"):
            self.signoff_svc.create_entry({
                "目标编号": f"G-{goal['id']}",
                "指导教师": "林老师",
                "阶段成果": "第一阶段听课记录",
            })

    def test_approved_goal_rejects_new_signoff(self):
        goal = make_goal(self.goal_svc, status="已审定")
        make_pair(self.pair_svc)
        with pytest.raises(ValueError, match="不再受理签认"):
            self.signoff_svc.create_entry({
                "目标编号": f"G-{goal['id']}",
                "指导教师": "林老师",
                "阶段成果": "第一阶段听课记录",
            })

    def test_submit_writes_conclusion_into_goal_todo(self):
        """提交签认后：结论写入学期目标待办，记录实际签认人与结对关系"""
        goal = make_goal(self.goal_svc, status="待审定")
        pair = make_pair(self.pair_svc)
        req, _ = self.signoff_svc.create_entry({
            "目标编号": f"G-{goal['id']}",
            "指导教师": "林老师",
            "阶段成果": "第一阶段听课记录",
            "签认结论": "阶段成果达标，同意进入审定",
        })
        result, msg = self.signoff_svc.run_action(req["id"], "提交签认")
        assert result is not None
        assert result["status"] == "已签认"
        assert result["签认人"] == "林老师"
        assert result["结对编号"] == f"P-{pair['id']}"
        # 结论写入学期目标待办
        goal_row = store.find("semester_goal", goal["id"])
        assert "阶段成果达标，同意进入审定" in goal_row["备注说明"]
        assert "林老师" in goal_row["备注说明"]
        assert goal_row["pending"] is True

    def test_submit_requires_conclusion(self):
        goal = make_goal(self.goal_svc, status="待审定")
        make_pair(self.pair_svc)
        req, _ = self.signoff_svc.create_entry({
            "目标编号": f"G-{goal['id']}",
            "指导教师": "林老师",
            "阶段成果": "第一阶段听课记录",
        })
        result, msg = self.signoff_svc.run_action(req["id"], "提交签认")
        assert result is None
        assert "签认结论" in msg
        # 提交时可通过备注补结论
        result2, _ = self.signoff_svc.run_action(req["id"], "提交签认", remark="补充的阶段意见")
        assert result2 is not None
        assert result2["签认结论"] == "补充的阶段意见"

    def test_one_formal_signoff_per_goal_stage(self):
        """同一目标同一阶段成果只能有一张正式签认"""
        goal = make_goal(self.goal_svc, status="待审定")
        make_pair(self.pair_svc)
        req, _ = self.signoff_svc.create_entry({
            "目标编号": f"G-{goal['id']}",
            "指导教师": "林老师",
            "阶段成果": "第一阶段听课记录",
            "签认结论": "达标",
        })
        self.signoff_svc.run_action(req["id"], "提交签认")
        # 同目标同阶段不允许再发起
        with pytest.raises(ValueError, match="签认冲突"):
            self.signoff_svc.create_entry({
                "目标编号": f"G-{goal['id']}",
                "指导教师": "林老师",
                "阶段成果": "第一阶段听课记录",
                "签认结论": "再签一次",
            })

    def test_pairing_close_expires_inflight_signoff(self):
        """结对验收/终止后，凭该结对发出的在途签认失效"""
        goal = make_goal(self.goal_svc, status="待审定")
        pair = make_pair(self.pair_svc)
        req, _ = self.signoff_svc.create_entry({
            "目标编号": f"G-{goal['id']}",
            "指导教师": "林老师",
            "阶段成果": "第一阶段听课记录",
            "签认结论": "达标",
        })
        self.pair_svc.run_action(pair["id"], "终止结对")
        req_row = store.find("goal_signoff", req["id"])
        assert req_row["status"] == "已失效"
        result, msg = self.signoff_svc.run_action(req["id"], "提交签认")
        assert result is None
        assert "不可提交签认" in msg


class TestReassignmentConflict:
    """于秋 G-16 场景：改派与同一目标的在途确认发生冲突"""

    def setup_method(self):
        clear_all()
        self.signoff_svc = GoalSignoffService()
        self.pair_svc = MentorPairService()
        self.goal_svc = SemesterGoalService()
        # 青年教师于秋的秋季目标 G-16 待审定，原导师林老师已打开目标准备在途签认
        self.goal = make_goal(self.goal_svc, status="待审定")
        self.goal_ref = f"G-{self.goal['id']}"
        self.pair = make_pair(self.pair_svc, mentor="林老师")
        self.req, _ = self.signoff_svc.create_entry({
            "目标编号": self.goal_ref,
            "指导教师": "林老师",
            "阶段成果": "第一阶段听课记录与改进建议",
            "签认结论": "阶段成果达标",
        })

    def _reassign(self, new_mentor="王老师"):
        result, msg = self.pair_svc.run_action(self.pair["id"], "改派导师", remark=new_mentor)
        assert result is not None, msg
        return result, msg

    def test_reassign_transfers_inflight_signoff(self):
        _, msg = self._reassign()
        assert "接任名单" in msg
        req_row = store.find("goal_signoff", self.req["id"])
        assert req_row["status"] == "待接任"
        assert req_row["待接任教师"] == "王老师"
        # 原结对结项，新结对建立且学期不变
        assert store.find("mentor_pair", self.pair["id"])["status"] == "已结项"
        new_pairs = [r for r in store.rows("mentor_pair")
                     if r["指导教师"] == "王老师" and r["status"] == "进行中"]
        assert len(new_pairs) == 1
        assert new_pairs[0]["结对学期"] == "2026-秋季"
        assert new_pairs[0]["青年教师"] == "于秋"

    def test_old_mentor_submit_after_reassign_shows_not_his(self):
        """原导师尝试交意见时，要看到自己的那份不再由他签"""
        self._reassign()
        result, msg = self.signoff_svc.run_action(self.req["id"], "提交签认")
        assert result is None
        assert "不再由你签认" in msg
        assert "王老师" in msg

    def test_new_mentor_takeover_list_and_submit(self):
        """新导师从接任名单查到这笔，接任后提交成为唯一正式签认"""
        self._reassign()
        # 接任名单：新导师带视角只能看到待自己接任的这笔
        rows, total = self.signoff_svc.list_entries(viewer="王老师")
        assert total == 1
        assert rows[0]["id"] == self.req["id"]
        assert rows[0]["status"] == "待接任"
        # 接任
        result, msg = self.signoff_svc.run_action(self.req["id"], "接任签认")
        assert result is not None
        assert result["status"] == "待提交"
        assert result["指导教师"] == "王老师"
        new_pair = [r for r in store.rows("mentor_pair") if r["指导教师"] == "王老师"][0]
        assert result["结对编号"] == f"P-{new_pair['id']}"
        # 接任后提交：正式签认人是新导师
        result2, _ = self.signoff_svc.run_action(self.req["id"], "提交签认")
        assert result2 is not None
        assert result2["status"] == "已签认"
        assert result2["签认人"] == "王老师"
        # 原导师名下不再有可提交的在途签认
        old_rows, old_total = self.signoff_svc.list_entries(viewer="林老师")
        assert all(r["status"] != "待提交" for r in old_rows)

    def test_only_one_formal_signoff_reaches_director(self):
        """同一目标不会由两位导师各签一张：改派前后都只有一张正式签认"""
        self._reassign()
        self.signoff_svc.run_action(self.req["id"], "接任签认")
        self.signoff_svc.run_action(self.req["id"], "提交签认")
        formal = [r for r in store.rows("goal_signoff")
                  if r["目标编号"] == self.goal_ref and r["status"] == "已签认"]
        assert len(formal) == 1
        assert formal[0]["签认人"] == "王老师"

    def test_takeover_reject_closes_request(self):
        self._reassign()
        result, msg = self.signoff_svc.run_action(self.req["id"], "拒接签认")
        assert result is not None
        assert result["status"] == "已失效"
        # 拒接后该请求不能再提交
        result2, msg2 = self.signoff_svc.run_action(self.req["id"], "提交签认")
        assert result2 is None

    def test_reassign_requires_new_mentor_remark(self):
        result, msg = self.pair_svc.run_action(self.pair["id"], "改派导师")
        assert result is None
        assert "新任指导教师" in msg

    def test_reassign_conflict_when_new_mentor_occupied(self):
        make_pair(self.pair_svc, mentor="王老师", mentee="另一位青年教师")
        result, msg = self.pair_svc.run_action(self.pair["id"], "改派导师", remark="王老师")
        assert result is None
        assert "结对冲突" in msg


class TestDirectorApprovalCheck:
    """主任审定时从结对名单核对这个学期的签认请求"""

    def setup_method(self):
        clear_all()
        self.signoff_svc = GoalSignoffService()
        self.pair_svc = MentorPairService()
        self.goal_svc = SemesterGoalService()

    def _signed_goal(self):
        goal = make_goal(self.goal_svc, status="待审定")
        pair = make_pair(self.pair_svc)
        req, _ = self.signoff_svc.create_entry({
            "目标编号": f"G-{goal['id']}",
            "指导教师": "林老师",
            "阶段成果": "第一阶段听课记录",
            "签认结论": "达标",
        })
        self.signoff_svc.run_action(req["id"], "提交签认")
        return goal, pair, req

    def test_approve_with_consistent_signoff(self):
        goal, _, _ = self._signed_goal()
        result, msg = self.goal_svc.run_action(goal["id"], "审定目标")
        assert result is not None
        assert result["status"] == "已审定"

    def test_approve_blocked_when_signoff_pairing_tampered(self):
        """签认请求与结对名单核对不符时，审定被驳回"""
        goal, pair, req = self._signed_goal()
        # 结对名单里这笔结对的导师被改成别人，签认人与其不符
        pair["指导教师"] = "张老师"
        result, msg = self.goal_svc.run_action(goal["id"], "审定目标")
        assert result is None
        assert "核对不符" in msg
        assert str(req["id"]) in msg

    def test_approve_paused_while_takeover_pending(self):
        """改派未结（签认待接任）时，审定暂缓"""
        goal = make_goal(self.goal_svc, status="待审定")
        pair = make_pair(self.pair_svc)
        req, _ = self.signoff_svc.create_entry({
            "目标编号": f"G-{goal['id']}",
            "指导教师": "林老师",
            "阶段成果": "第一阶段听课记录",
            "签认结论": "达标",
        })
        self.pair_svc.run_action(pair["id"], "改派导师", remark="王老师")
        result, msg = self.goal_svc.run_action(goal["id"], "审定目标")
        assert result is None
        assert "审定暂缓" in msg
        # 新导师接任并提交后，审定恢复可办
        self.signoff_svc.run_action(req["id"], "接任签认")
        self.signoff_svc.run_action(req["id"], "提交签认")
        result2, _ = self.goal_svc.run_action(goal["id"], "审定目标")
        assert result2 is not None
        assert result2["status"] == "已审定"

    def test_approve_without_signoff_still_works(self):
        """没有签认请求的目标维持原有审定行为"""
        goal = make_goal(self.goal_svc, status="待审定")
        result, _ = self.goal_svc.run_action(goal["id"], "审定目标")
        assert result is not None
        assert result["status"] == "已审定"


class TestHistoryAndViewerScoping:
    """已审定目标不受换岗影响；同教研室老师只可查看自己的指导事项"""

    def setup_method(self):
        clear_all()
        self.signoff_svc = GoalSignoffService()
        self.pair_svc = MentorPairService()
        self.goal_svc = SemesterGoalService()

    def test_approved_goal_unaffected_by_reassignment(self):
        """上一次已审定的目标不受此次换岗影响，重新打开仍能查到实际签认人及结对关系"""
        # 春季：林老师结对于秋，目标已签认并审定
        spring_goal = make_goal(self.goal_svc, goal_id_semester="2026-春季", status="待审定",
                                desc="春季课堂常规达标")
        spring_pair = make_pair(self.pair_svc, semester="2026-春季", date_range="2026-02至2026-07")
        spring_req, _ = self.signoff_svc.create_entry({
            "目标编号": f"G-{spring_goal['id']}",
            "指导教师": "林老师",
            "阶段成果": "春季阶段成果",
            "签认结论": "春季达标",
        })
        self.signoff_svc.run_action(spring_req["id"], "提交签认")
        self.goal_svc.run_action(spring_goal["id"], "审定目标")
        spring_pair["status"] = "已结项"

        # 秋季：新目标 + 新结对，随后改派导师
        autumn_goal = make_goal(self.goal_svc, goal_id_semester="2026-秋季", status="待审定")
        autumn_pair = make_pair(self.pair_svc, semester="2026-秋季", date_range="2026-09至2027-01")
        self.pair_svc.run_action(autumn_pair["id"], "改派导师", remark="王老师")

        # 春季已审定目标与签认保持原样
        spring_goal_row = store.find("semester_goal", spring_goal["id"])
        assert spring_goal_row["status"] == "已审定"
        spring_req_row = store.find("goal_signoff", spring_req["id"])
        assert spring_req_row["status"] == "已签认"
        assert spring_req_row["签认人"] == "林老师"
        assert spring_req_row["结对编号"] == f"P-{spring_pair['id']}"
        # 重新打开仍能查到实际签认人及结对关系
        pair_row = store.find("mentor_pair", spring_pair["id"])
        assert pair_row["指导教师"] == "林老师"
        assert pair_row["结对学期"] == "2026-春季"

    def test_viewer_only_sees_own_guidance_items(self):
        """同教研室的其他老师只可查看自己的指导事项"""
        goal1 = make_goal(self.goal_svc, teacher="于秋", status="待审定")
        make_pair(self.pair_svc, mentor="林老师", mentee="于秋")
        req1, _ = self.signoff_svc.create_entry({
            "目标编号": f"G-{goal1['id']}",
            "指导教师": "林老师",
            "阶段成果": "第一阶段听课记录",
            "签认结论": "达标",
        })
        goal2 = make_goal(self.goal_svc, teacher="小赵", status="待审定", desc="另一个目标")
        make_pair(self.pair_svc, mentor="周老师", mentee="小赵")
        req2, _ = self.signoff_svc.create_entry({
            "目标编号": f"G-{goal2['id']}",
            "指导教师": "周老师",
            "阶段成果": "第一阶段听课记录",
            "签认结论": "达标",
        })

        # 林老师只看到自己的指导事项
        rows, total = self.signoff_svc.list_entries(viewer="林老师")
        assert total == 1
        assert rows[0]["id"] == req1["id"]
        # 周老师只看到自己的
        rows2, total2 = self.signoff_svc.list_entries(viewer="周老师")
        assert total2 == 1
        assert rows2[0]["id"] == req2["id"]
        # 主任不带视角可核对全部
        _, total_all = self.signoff_svc.list_entries()
        assert total_all == 2
