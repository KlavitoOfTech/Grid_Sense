-- GridSense Phase 4, Stage 1: onboarding fields and energy settings

-- ---------------------------------------------------------------------------
-- profiles: onboarding details (the row already exists from handle_new_user)
-- ---------------------------------------------------------------------------
alter table public.profiles
  add column if not exists account_name text,
  add column if not exists user_type text,
  add column if not exists city text,
  add column if not exists state text,
  add column if not exists onboarding_completed boolean not null default false;

alter table public.profiles drop constraint if exists profiles_user_type_check;
alter table public.profiles
  add constraint profiles_user_type_check
  check (user_type is null or user_type in ('household', 'business'));

-- ---------------------------------------------------------------------------
-- energy_settings: currency and fuel-based generator cost
-- ---------------------------------------------------------------------------
alter table public.energy_settings
  add column if not exists currency text not null default 'NGN',
  add column if not exists generator_cost_mode text not null default 'per_kwh',
  add column if not exists fuel_price_per_litre numeric,
  add column if not exists gen_litres_per_kwh numeric;

-- In fuel mode there is no per-kWh rate, so it must be allowed to be empty
alter table public.energy_settings alter column generator_rate drop not null;

alter table public.energy_settings drop constraint if exists energy_settings_currency_check;
alter table public.energy_settings
  add constraint energy_settings_currency_check check (currency ~ '^[A-Z]{3}$');

alter table public.energy_settings drop constraint if exists energy_settings_mode_check;
alter table public.energy_settings
  add constraint energy_settings_mode_check check (
    generator_cost_mode in ('per_kwh', 'fuel')
  );

alter table public.energy_settings drop constraint if exists energy_settings_fuel_values_check;
alter table public.energy_settings
  add constraint energy_settings_fuel_values_check check (
    (fuel_price_per_litre is null or fuel_price_per_litre >= 0)
    and (gen_litres_per_kwh is null or gen_litres_per_kwh > 0)
  );

-- Each mode must carry the numbers it needs
alter table public.energy_settings drop constraint if exists energy_settings_mode_fields_check;
alter table public.energy_settings
  add constraint energy_settings_mode_fields_check check (
    (generator_cost_mode = 'per_kwh' and generator_rate is not null)
    or
    (generator_cost_mode = 'fuel'
       and fuel_price_per_litre is not null
       and gen_litres_per_kwh is not null)
  );