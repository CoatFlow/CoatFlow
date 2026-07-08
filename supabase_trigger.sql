-- ============================================================================
-- CoatFlow — Auto-provisioning trigger
-- ----------------------------------------------------------------------------
-- Doel: elke NIEUWE gebruiker krijgt ALTIJD direct een bedrijf + koppeling in
--       public.app_users (met company_id), ook al moet hij zijn e-mail nog
--       bevestigen. Dit gebeurt op databaseniveau (trigger), niet in Python —
--       cruciaal voor de RLS-isolatie en betrouwbaar bij e-mailbevestiging/OAuth.
--
-- Draai dit ÉÉNMALIG in de Supabase SQL-editor (idempotent, veilig te herhalen).
-- ============================================================================

-- Functie: maak bij een nieuwe auth.users-rij een bedrijf + app_users-koppeling.
-- SECURITY DEFINER → draait met de rechten van de eigenaar (postgres), zodat hij
-- in public.companies / public.app_users mag schrijven ondanks RLS.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    new_company_id uuid := gen_random_uuid();
begin
    -- 1. Nieuw, leeg bedrijf voor deze gebruiker
    insert into public.companies (id, naam)
    values (new_company_id, 'Mijn bedrijf');

    -- 2. Koppel de gebruiker als eigenaar aan dat bedrijf
    insert into public.app_users (id, company_id, email, role)
    values (new.id, new_company_id, new.email, 'owner');

    return new;
end;
$$;

-- Trigger: vuur ná het aanmaken van een auth-gebruiker.
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row
    execute function public.handle_new_user();

-- Mag de auth-service de functie uitvoeren (defensief; trigger werkt doorgaans
-- ook zonder, maar dit voorkomt rechten-verrassingen).
grant execute on function public.handle_new_user() to supabase_auth_admin;

-- ============================================================================
-- CONTROLE: registreer een testaccount → er hoort meteen een rij te staan in
--   public.app_users (met company_id) én een nieuw bedrijf in public.companies,
--   nog vóór de e-mail is bevestigd.
-- Bestaande gebruikers worden NIET geraakt (de trigger vuurt alleen op nieuwe).
-- ============================================================================
