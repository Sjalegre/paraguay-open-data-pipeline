-- The department mart must sum to the district fact it is built from.
--
-- An aggregation that silently drops rows -- an inner join that should have
-- been a left join, a group-by that loses nulls -- is invisible in the output
-- and shows up here.

with districts as (
    select year, sum(population) as population
    from {{ ref('fct_district_profile') }}
    group by 1
),

departments as (
    select year, sum(population) as population
    from {{ ref('mart_department_summary') }}
    group by 1
)

select
    d.year,
    d.population as district_total,
    p.population as department_total
from districts as d
inner join departments as p using (year)
where abs(d.population - p.population) > 0.5
