import { useEffect, useState } from "react";
import "./splash.css";

export default function Splash({ message = "Just a moment…" }) {
  const [slow, setSlow] = useState(false);

  // Render's free tier sleeps when idle; reassure the user instead of leaving a blank wait
  useEffect(() => {
    const t = setTimeout(() => setSlow(true), 6000);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className="splash" role="status" aria-live="polite">
      <div className="splash-logo">
        <span className="splash-ring" />
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8z" />
        </svg>
      </div>
      <div className="splash-name">GridSense</div>
      <p className="splash-msg">{message}</p>
      <div className="splash-bar"><i /></div>
      {slow && (
        <p className="splash-slow">
          Waking things up. This can take up to a minute the first time.
        </p>
      )}
    </div>
  );
}