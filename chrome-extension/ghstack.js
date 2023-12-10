"use strict";
// Check if this is a ghstack PR
for (const e of document.getElementsByClassName("base-ref")) {
  if (e.innerText.match(/^gh\//)) {
    // It is, delete the merge message (which contains the button)
    for (const e of document.getElementsByClassName("merge-message")) {
      e.remove();
    }
    break;
  }
}
