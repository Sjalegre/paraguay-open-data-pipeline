-- One row per district: everything the 2022 census and the NBI table say about
-- it, side by side.
--
-- The warehouse stores observations long -- one row per district, indicator and
-- year -- because that lets indicators be added without a schema change. A
-- reader and a BI tool both want them wide. Pivoting here rather than in the
-- warehouse keeps the fact table general and puts the convenience in the mart,
-- which is what marts are for.

with observations as (
    select * from {{ ref('stg_district_indicators') }}
),

pivoted as (
    select
        geo_key,
        year,
        max(case when indicator_code = 'POP_TOTAL'  then value end) as population,
        max(case when indicator_code = 'POP_MALE'   then value end) as population_male,
        max(case when indicator_code = 'POP_FEMALE' then value end) as population_female,
        max(case when indicator_code = 'AGE_MEDIAN' then value end) as median_age,
        max(case when indicator_code = 'NBI_HOUSEHOLDS'      then value end) as households,
        max(case when indicator_code = 'NBI_ANY_PCT'         then value end) as nbi_any_pct,
        max(case when indicator_code = 'NBI_HOUSING_PCT'     then value end) as nbi_housing_pct,
        max(case when indicator_code = 'NBI_SANITATION_PCT'  then value end) as nbi_sanitation_pct,
        max(case when indicator_code = 'NBI_EDUCATION_PCT'   then value end) as nbi_education_pct,
        max(case when indicator_code = 'NBI_SUBSISTENCE_PCT' then value end) as nbi_subsistence_pct
    from observations
    group by 1, 2
)

select
    g.geo_key,
    g.department_code,
    g.department_name,
    g.district_name,
    g.reconciles_to_2012,
    g.is_new_since_2012,
    g.has_encoding_damage,

    p.year,
    p.population,
    p.population_male,
    p.population_female,
    p.median_age,
    p.households,
    p.nbi_any_pct,
    p.nbi_housing_pct,
    p.nbi_sanitation_pct,
    p.nbi_education_pct,
    p.nbi_subsistence_pct,

    -- A percentage says how concentrated deprivation is; this says how many
    -- households it is. Ranking on the rate alone puts tiny Chaco districts
    -- above Ciudad del Este, which is true and not useful.
    round(p.households * p.nbi_any_pct / 100.0)          as households_with_nbi,
    p.population / nullif(p.households, 0)               as persons_per_household,
    p.population_male / nullif(p.population_female, 0)   as men_per_woman

from {{ ref('stg_geography') }} as g
inner join pivoted as p
    on g.geo_key = p.geo_key
