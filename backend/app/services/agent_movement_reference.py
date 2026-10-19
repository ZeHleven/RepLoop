"""Small, source-attributed movement facts, not personal medical clearance.

No name-based generalization to other variants or automatic exercise approval.
The dated references are deliberately separate from account/tool observations.
"""
import copy

_REFERENCE={
    'version':1,
    'reviewed_on':'2026-10-14',
    'source_kind':'curated_general_movement_reference_not_user_data',
    'coverage':'two_explicit_variants_only_not_a_complete_exercise_catalogue',
    'facts':[
        {
            'id':'flat_barbell_chest_press',
            'variant':'平板杠铃卧推',
            'fact':'平卧在凳上，双手握杠，杠向胸部下降后再推起。',
            'source_url':'https://www.acefitness.org/resources/everyone/exercise-library/5/chest-press/',
            'source_title':'ACE Exercise Library — Chest Press',
            'interpretation':'按此动作描述，平板卧推不能等同于站立抬手过头或过头推举；这是动作区分，不是肩痛时可以卧推的证明。',
        },
        {
            'id':'dumbbell_goblet_squat',
            'variant':'哑铃高脚杯深蹲',
            'fact':'在胸前持哑铃完成深蹲，动作仍包含手持外部负重。',
            'source_url':'https://www.acefitness.org/resources/everyone/exercise-library/362/goblet-squat/',
            'source_title':'ACE Exercise Library — Goblet Squat',
            'interpretation':'属于深蹲或改成胸前持重，不代表取消了抓握/持重环节；不能据此推出当前手腕可耐受。',
        },
    ],
    'personal_tolerance':'unknown_unless_supported_by_current_user_report',
    'other_variants':'not_covered_do_not_infer_from_names',
}

def movement_reference(query=None):
    result=copy.deepcopy(_REFERENCE)
    if query is not None:
        # A reference requested by name is not confirmation of the user's
        # actual variant. Do not suggest a barbell merely from generic 卧推.
        aliases={'flat_barbell_chest_press':('平板卧推','平板杠铃卧推'),
                 'dumbbell_goblet_squat':('高脚杯','胸前抱哑铃')}
        result['facts']=[f for f in result['facts'] if any(a in query for a in aliases[f['id']])]
        result['selection']='explicitly_mentioned_variant_only_not_personal_confirmation'
    return result
