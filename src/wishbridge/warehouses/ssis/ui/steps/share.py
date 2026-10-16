"""Assessment: share the zip with Wishtree (WishBridge never sends it itself)."""

from __future__ import annotations

import os
import subprocess
import sys

import pandas as pd
import streamlit as st

from wishbridge.warehouses.ssis.state import load_state
from wishbridge.warehouses.ssis.ui import helpers as h

ss = st.session_state


def render(cfg, raw, packages) -> None:
    _render(cfg, raw, packages)
    from wishbridge.warehouses.ssis.ui.steps.report import next_phase_button

    next_phase_button(cfg, "to-code-migration-share")


def _render(cfg, raw, packages) -> None:
    from wishbridge.warehouses.ssis import mailer

    st.subheader("Share the zip")
    if not packages:
        st.info("No zip yet - run *Assess and save everything* first.")
    else:
        pkg = packages[-1]
        size_mb = pkg.stat().st_size / (1024 * 1024)
        st.markdown("Everything from this visit is in **one zip file**. WishBridge does not send it anywhere: "
                    "the client's team shares it with Wishtree the way they prefer (e-mail, OneDrive, SharePoint...).")
        st.markdown(f"**File:** `{pkg.name}` · {size_mb:.1f} MB  \n**Location:** `{pkg}`")
        b1, b2 = st.columns(2)
        if b1.button("📂 Show the zip in its folder"):
            if os.name == "nt":
                subprocess.Popen(["explorer", "/select,", str(pkg)])
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(pkg.parent)])
        b2.download_button("⬇ Download the zip", pkg.read_bytes(), file_name=pkg.name, mime="application/zip")

        st.markdown("**What is inside**")
        st.table(pd.DataFrame(mailer.CONTENTS, columns=["In the zip", "What it is"]).set_index("In the zip"))

        st.markdown("**Who to send it to**")
        share = raw.get("share") or {}
        c1, c2 = st.columns(2)
        to_text = c1.text_input("Send to (Wishtree)", value=", ".join(share.get("to", [])),
                                placeholder="you@wishtreetech.com")
        cc_text = c2.text_input("CC (optional)", value=", ".join(share.get("cc", [])), placeholder="name@client.com")
        to, cc = mailer.split_addresses(to_text), mailer.split_addresses(cc_text)
        bad = mailer.invalid_addresses(to + cc)
        if bad:
            st.error("Not a valid e-mail address: " + ", ".join(bad))
        elif (to, cc) != (share.get("to", []), share.get("cc", [])) and to:
            new = dict(raw)
            new["share"] = {"to": to, "cc": cc}
            h.save_raw(ss.project, new)  # remembered for this project

        too_big = size_mb > mailer.MAX_ATTACHMENT_MB
        link = ""
        if too_big:
            st.markdown("**Share it through OneDrive**")
            st.warning(f"The zip is {size_mb:.0f} MB - too big for e-mail (the usual limit is {mailer.MAX_ATTACHMENT_MB} MB). "
                       "Share it through OneDrive and send the link instead.")
            od = mailer.onedrive_folder()
            if od is not None:
                st.markdown("1. Press **Put the zip in OneDrive** - it goes to the *WishBridge* folder and OneDrive uploads it.\n"
                            "2. In the folder that opens, right-click the zip → **Share** → **Copy link**.\n"
                            "3. Paste the link below and send the e-mail.")
                if st.button("☁ Put the zip in OneDrive"):
                    try:
                        with st.spinner("Copying to OneDrive..."):
                            dest = mailer.copy_to_onedrive(pkg)
                        if os.name == "nt":
                            subprocess.Popen(["explorer", "/select,", str(dest)])
                        st.success(f"Copied to `{dest}`. OneDrive is uploading it - share it once the upload has finished.")
                    except OSError as e:
                        st.error(f"Could not copy it: {e}")
            else:
                st.markdown("OneDrive is not set up on this computer:\n"
                            "1. Press **Open OneDrive** and sign in with the company account.\n"
                            "2. Upload the zip (use *Show the zip in its folder* above to find it).\n"
                            "3. Share it → **Copy link**, paste the link below and send the e-mail.")
                st.link_button("☁ Open OneDrive", "https://www.office.com/launch/onedrive")
            link = st.text_input("OneDrive link to the zip", placeholder="https://...sharepoint.com/... or https://1drv.ms/...",
                                 key="share_link").strip()
            if link and not link.lower().startswith("https://"):
                st.error("The link should start with https://")

        subject, body = mailer.default_message(cfg.name, load_state(cfg), pkg.name, link)
        st.markdown("**Ready-made message for the client's team** (copy with the button at the top right of the box)")
        handover = (f"To: {', '.join(to) or '<Wishtree e-mail>'}" + (f"\nCC: {', '.join(cc)}" if cc else "")
                    + f"\nSubject: {subject}" + ("" if too_big else f"\nAttach: {pkg}") + f"\n\n{body}")
        st.code(handover, language=None)
        ss[f"sent:{ss.project}"] = True

        st.divider()
        if not too_big:
            label, ready, attach = "📧 Send mail with the zip attached", bool(to) and not bad, pkg
            hint = "Opens a new e-mail with To, CC, subject, message and the zip attached."
        else:
            label, ready, attach = "📧 Send mail with the OneDrive link", bool(to) and not bad and link.startswith("https://"), None
            hint = "Opens a new e-mail with To, CC, subject and the message including the OneDrive link."
        if st.button(label, type="primary", disabled=not ready,
                     help=hint + " Nothing is sent until you check it and press Send in your mail program."):
            ok, msg = mailer.open_mail_draft(to, cc, subject, body, attach)
            (st.success if ok else st.error)(msg)
        if not to:
            st.caption("Enter the Wishtree e-mail in *Send to* above to use this button.")
        elif too_big and not link:
            st.caption("Paste the OneDrive link above to use this button.")
