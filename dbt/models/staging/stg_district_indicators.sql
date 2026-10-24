-- District-level observations, joined to their indicator metadata.

select
    f.geo_key,
    f.indicator_code,
    i.indicator_name,
    i.unit,
    i.publisher,
    f.year,
    f.value,
    f.source_key,
    f.run_id
from {{ source('warehouse', 'fact_indicator_district') }} as f
inner join {{ source('warehouse', 'dim_indicator') }} as i
    on f.indicator_code = i.indicator_code
