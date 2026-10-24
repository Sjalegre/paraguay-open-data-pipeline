-- Quality-check results per run, shaped for a dashboard.
--
-- `fact_quality_check` is a fact table rather than a log, so the trend across
-- runs is a plain aggregation. This is what the Power BI quality page reads.

select
    q.run_id,
    r.started_at,
    r.pipeline_version,
    q.check_family,
    count(*)                                              as checks,
    count(*) filter (where q.status = 'pass')             as passed,
    count(*) filter (where q.status = 'fail')             as failed,
    count(*) filter (where q.status = 'fail'
                       and q.severity = 'error')          as blocking,
    count(*) filter (where q.status = 'fail'
                       and q.severity = 'warning')        as warnings,
    sum(q.failing_rows)                                   as failing_rows
from {{ source('warehouse', 'fact_quality_check') }} as q
inner join {{ source('warehouse', 'dim_run') }} as r
    on q.run_id = r.run_id
group by 1, 2, 3, 4
