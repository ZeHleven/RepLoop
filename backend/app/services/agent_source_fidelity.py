"""Preserve an untyped symptom value rather than inventing a time unit."""
import re


def preserve_symptom_measurements(route, message):
    if (route.request_kind not in {'query','assessment'} or route.requested_effect!='read'
            or not re.search(r'疼|痛|酸|麻|肿',message)):
        return route
    # Only isolated reports such as “约2分”. “疼了2分” can mean duration;
    # this guard neither diagnoses nor guesses a pain scale from bare 分.
    reports={}
    for clause in re.split(r'[，,。；;！!？?\n]',message):
        m=re.fullmatch(r'\s*((?:约|大约)?\s*(\d+(?:\.\d+)?)\s*分)\s*',clause)
        if m:reports[m[2]]=m[1]
    if not reports:return route
    text=route.normalized_request
    for value,quote in reports.items():
        if re.search(r'(?<![\d.])'+re.escape(value)+r'\s*分钟',message):continue
        # Retain every other goal in the clause. Remove only the invented
        # duration and its adjacent qualifier, including “持续”.
        text=re.sub(r'(?:(?:持续(?:了)?|时长(?:为)?|时间(?:为)?)\s*)?(?:大约|约)?\s*(?<![\d.])'+re.escape(value)+r'\s*分钟',
                    lambda _: '用户报告原文“'+quote+'”，该数字的量纲未明确，不能作为时长',text)
    update=route.task_update
    if update:
        edits=[]
        for edit in update.requirements:
            key=edit.key
            if edit.quote in reports.values() and not edit.remove and 'duration' in key:
                key=key.replace('duration','reported_value')
            edits.append(edit.model_copy(update={'key':key}))
        update=update.model_copy(update={'requirements':edits})
    return route.model_copy(update={'normalized_request':text,'task_update':update})
