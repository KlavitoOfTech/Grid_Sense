import SettingsForm from "./SettingsForm";

export default function Onboarding({ user, settings, onDone, onLogout }) {
  return (
    <main>
      <header>
        <span className="mark">GridSense</span>
        <span className="hright">
          <span className="muted">{user?.email}</span>
          <button className="chip" onClick={onLogout}>Log out</button>
        </span>
      </header>
      <h1>Let’s set up your energy profile.</h1>
      <p className="lede">Two quick steps. This lets GridSense price your power correctly. You can change everything later in Settings.</p>
      <SettingsForm wizard initial={settings} submitLabel="Finish setup" onSaved={onDone} />
    </main>
  );
}