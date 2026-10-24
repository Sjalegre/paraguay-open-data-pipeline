-- National annual series, joined to their indicator metadata.

select
    f.indicator_code,
    i.indicator_name,
    i.unit,
    i.expect_complete_from,
    f.year,
    f.value,
    f.source_key,
    f.run_id
from {{ source('warehouse', 'fact_indicator_national') }} as f
inner join {{ source('warehouse', 'dim_indicator') }} as i
    on f.indicator_code = i.indicator_code
