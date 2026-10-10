/* AccessSlice - the app's JavaScript, loaded on every page.
 *
 * Three small jobs, all in service of screen-reader comfort:
 *
 * 1. FOCUS RETENTION. Every action is a form post followed by a fresh
 *    page load, and a fresh page normally dumps focus at the top - which
 *    means losing your place after every save. So: before any form
 *    submits, remember which control had focus; after the next page
 *    loads, put focus straight back on that control (or the nearest
 *    equivalent that still exists).
 *
 * 2. ANNOUNCEMENTS. The server renders the result message into the
 *    status live region, but screen readers only announce live-region
 *    CHANGES, not text that was already there at load. So the message is
 *    briefly cleared and re-inserted, which makes it announce reliably -
 *    without ever moving focus.
 *
 * 3. The waiting screen's once-a-second poll of the folder watcher,
 *    the building and slicing screens' polls, and the printer page's
 *    half-minute poll while a print runs.
 *
 * Everything works without JavaScript too; these are comfort layers.
 */

(function () {
  "use strict";

  /* ------------------------------------------------ focus retention */

  // Remember the focused control (by id) whenever a form submits.
  document.addEventListener("submit", function (e) {
    try {
      var active = document.activeElement;
      var id = active && active.id ? active.id : "";
      // A submit button disappears into the action; its form's first
      // field is a better place to come back to than the page top.
      if (active && active.type === "submit" && !id) {
        var field = e.target.querySelector("input, select, textarea");
        if (field && field.id) id = field.id;
      }
      sessionStorage.setItem("accessslice-focus", id);
    } catch (err) { /* storage unavailable - focus simply stays at top */ }
  });

  // Restore it on the next page. When the control is gone or DISABLED
  // now (the Save button disables once everything is saved), stay in
  // the same NEIGHBOURHOOD - the nearest focusable control beside it,
  // in practice the Review link right next to Save - never the top of
  // the page (Edis, 2026-08-27: "it jumped all the way to the top").
  // Wherever focus lands, a copy of the result message is placed
  // directly AFTER it, so one arrow-down reads the result.
  // A page that names its own first control (autofocus - the slicer's
  // yes/no questions put Yes first, like the dialogs they stand in
  // for) keeps it: the retention must not park focus on the status
  // line instead, and nothing is echoed after the button - the
  // question is the page (Edis, 2026-09-21).
  var autoFirst = document.querySelector("[autofocus]");
  try {
    var wanted = sessionStorage.getItem("accessslice-focus");
    if (autoFirst) {
      sessionStorage.removeItem("accessslice-focus");
      if (typeof autoFirst.focus === "function") autoFirst.focus();
      wanted = "";
    }
    if (wanted) {
      sessionStorage.removeItem("accessslice-focus");
      var el = document.getElementById(wanted);

      var echoAfter = function (target) {
        var statusEl = document.getElementById("status");
        var msgText = statusEl && statusEl.getAttribute("data-msg");
        if (msgText) {
          var echo = document.createElement("p");
          echo.className = "inline-status";
          echo.textContent = "Result: " + msgText;
          target.insertAdjacentElement("afterend", echo);
        }
      };

      if (el && !el.disabled && typeof el.focus === "function") {
        el.focus();
        echoAfter(el);
      } else {
        var near = null;
        if (el && el.closest) {
          // Prefer the bottom bar (where Save lives), then any
          // surrounding form or section, for a nearby control that
          // still works. Hidden inputs are not landing spots.
          var area = el.closest(".bottom-bar") ||
                     el.closest("form, section, main");
          if (area) {
            near = area.querySelector(
              "a[href], button:not([disabled]), " +
              "input:not([disabled]):not([type=hidden]), select");
          }
        }
        if (!near) near = document.getElementById("nav-review");
        if (near && typeof near.focus === "function") {
          near.focus();
          echoAfter(near);
        } else {
          var fallback = document.getElementById("status");
          if (fallback && fallback.getAttribute("data-msg")) {
            fallback.focus();
          }
        }
      }
    }
  } catch (err) { /* fine */ }

  /* --------------------------------------------- the coffee pop-up */

  // The server renders the monthly coffee dialog already open, so it
  // is readable without script. Here it becomes a real modal: closed
  // and reopened with showModal(), which the screen reader announces
  // as a dialog and which keeps the focus inside it. Escape is "Not
  // now" - the later form is posted, so the pop-up rests a month
  // instead of returning on the next visit to Home.
  var coffeeDialog = document.getElementById("coffee-dialog");
  if (coffeeDialog && typeof coffeeDialog.showModal === "function") {
    try {
      coffeeDialog.close();
      coffeeDialog.showModal();
      var coffeeFirst = document.getElementById("coffee-open");
      if (coffeeFirst) coffeeFirst.focus();
    } catch (err) { /* stays an open, non-modal dialog */ }
    coffeeDialog.addEventListener("cancel", function (e) {
      e.preventDefault();
      var later = document.getElementById("coffee-later-form");
      if (later) later.submit();
    });
  }

  /* -------------------------------------------------- announcements */

  // Never blank the status region while focus is ON it - clearing the
  // focused element mid-read is what threw NVDA to the page top. And
  // never re-announce over a page's own first control (autofocus):
  // a live region speaking a quarter second after the Yes button
  // took focus was heard INSTEAD of the button's name.
  var status = document.getElementById("status");
  if (status && status.getAttribute("data-msg") && !autoFirst
      && document.activeElement !== status) {
    var text = status.getAttribute("data-msg");
    status.textContent = "";
    setTimeout(function () { status.textContent = text; }, 250);
  }

  /* ------------------------------------------- settings: slicer */

  // Choosing another slicer swaps the program path field to THAT
  // slicer's path (last saved, else found in its standard place; the
  // server put it on the radio as data-exe) and says so in the note
  // above the field, which is a polite live region. Without script
  // the save makes the same swap. The field is only replaced when it
  // still holds a known slicer's path, never a hand-typed one.
  var exeField = document.getElementById("slicer_exe");
  var slicerRadios = document.querySelectorAll('input[name="slicer"]');
  if (exeField && slicerRadios.length) {
    var knownPaths = [];
    slicerRadios.forEach(function (r) {
      if (r.getAttribute("data-exe")) knownPaths.push(r.getAttribute("data-exe"));
    });
    slicerRadios.forEach(function (radio) {
      radio.addEventListener("change", function () {
        if (!radio.checked) return;
        var path = radio.getAttribute("data-exe") || "";
        var name = radio.getAttribute("data-name") || "the slicer";
        var current = exeField.value.trim();
        if (current && knownPaths.indexOf(current) === -1) return;
        exeField.value = path;
        var note = document.getElementById("exe-note");
        if (note) {
          note.textContent = path
            ? "Path switched to " + name + " at " + path + "."
            : "AccessSlice did not find " + name + " in its standard " +
              "install places - type the full path to its program.";
        }
      });
    });
  }

  /* ------------------------------------------- control-page poll */

  // While a command runs on the printer, ask /api/control every two
  // seconds, add each new step to the list, read the newest into the
  // polite live region, put a question from the printer's screen
  // ("press the knob") into the alert region, and when the command
  // ends say so in the alert region, refresh the printer's status
  // lines once from /api/printer, and stop.
  var controlLine = document.getElementById("control-line");
  if (controlLine) {
    var controlEvery = parseInt(
      controlLine.getAttribute("data-control-poll"), 10) || 2;
    var controlSeen = 0;
    var controlList = document.getElementById("control-events");
    if (controlList) controlSeen = controlList.children.length;
    var controlAlert = document.getElementById("control-alert");
    var attentionSaid = false;

    var refreshStatus = function () {
      fetch("/api/printer")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var list = document.getElementById("printer-lines");
          if (list && data.ok && data.lines) {
            list.textContent = "";
            data.lines.forEach(function (text) {
              var li = document.createElement("li");
              li.textContent = text;
              list.appendChild(li);
            });
          }
        })
        .catch(function () {});
    };

    var controlPoll = function () {
      fetch("/api/control")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          var events = data.events || [];
          if (controlList) {
            for (var i = controlSeen; i < events.length; i++) {
              var li = document.createElement("li");
              li.textContent = events[i].when + " " + events[i].message;
              controlList.appendChild(li);
            }
          }
          if (events.length > controlSeen) {
            controlLine.textContent = events[events.length - 1].message;
          }
          controlSeen = events.length;
          if (data.attention && !attentionSaid && controlAlert) {
            controlAlert.textContent =
              "The printer is asking a question on its screen. " +
              "Press the knob on the printer.";
            attentionSaid = true;
          } else if (!data.attention) {
            attentionSaid = false;
          }
          if (data.done) {
            var ended = (data.result === "finished")
              ? "Done: " + data.name + "."
              : "Not done: " + data.name + ". " + (data.text || "");
            if (controlAlert) controlAlert.textContent = ended;
            controlLine.textContent = ended;
            document.title = ended + " - AccessSlice";
            refreshStatus();
            return;
          }
          setTimeout(controlPoll, controlEvery * 1000);
        })
        .catch(function () {
          controlLine.textContent =
            "Lost contact with AccessSlice - is the program still " +
            "running?";
        });
    };

    setTimeout(controlPoll, controlEvery * 1000);
    return;
  }

  /* -------------------------------------------- printer-page poll */

  // While a job runs, ask the printer every 30 seconds (through
  // /api/printer), refresh the list, read a short progress line, and
  // announce the end of the print in an alert region. Errors are said
  // in words and polling stops; "Check again" is always there.
  var printerLine = document.getElementById("printer-line");
  if (printerLine) {
    var every = parseInt(printerLine.getAttribute("data-printer-poll"),
                         10) || 30;

    var printerPoll = function () {
      fetch("/api/printer")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (!data.ok) {
            printerLine.textContent =
              "Lost contact with the printer: " + data.error +
              " Use Check again in a moment.";
            return;
          }
          var list = document.getElementById("printer-lines");
          if (list && data.lines) {
            list.textContent = "";
            data.lines.forEach(function (text) {
              var li = document.createElement("li");
              li.textContent = text;
              list.appendChild(li);
            });
          }
          if (data.done) {
            var alertEl = document.getElementById("printer-alert");
            var ended = (data.phase === "finished")
              ? "The print has finished."
              : (data.phase === "idle")
                ? "The print has ended; the printer is idle."
                : "The print has stopped.";
            if (alertEl) alertEl.textContent = ended;
            printerLine.textContent = ended + " " + data.text;
            document.title = ended + " - AccessSlice";
            return;
          }
          printerLine.textContent = data.text;
          setTimeout(printerPoll, every * 1000);
        })
        .catch(function () {
          printerLine.textContent =
            "Lost contact with AccessSlice - is the program still " +
            "running?";
        });
    };

    setTimeout(printerPoll, every * 1000);
    return;
  }

  /* ------------------------------------------ building-screen poll */

  // The new-job build's progress page: the slicing page's rhythm -
  // poll every second, speak the stage and time every 15 seconds,
  // move on the moment the build ends (the server then opens the
  // project, or the Start page says why not).
  var buildLine = document.getElementById("build-line");
  if (buildLine) {
    var buildTicks = 0;

    var buildPoll = function () {
      fetch("/api/build")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status !== "running") {
            window.location = data.next || "/building";
            return;
          }
          buildTicks += 1;
          if (buildTicks % 15 === 0 && data.text) {
            buildLine.textContent = data.text;
          }
          setTimeout(buildPoll, 1000);
        })
        .catch(function () {
          buildLine.textContent =
            "Lost contact with AccessSlice - is the program still " +
            "running?";
        });
    };

    setTimeout(buildPoll, 1000);
    return;
  }

  /* ------------------------------------------- slicing-screen poll */

  // The in-app slicing progress page: poll the job every second, speak
  // a heartbeat every 15 seconds (progress without constant chatter),
  // and move to the report the moment the engine ends. Works without
  // this too - the page's "Check progress" link reloads, and the
  // server redirects to the report once the job is over.
  var sliceLine = document.getElementById("slice-line");
  if (sliceLine) {
    var sliceTicks = 0;

    var slicePoll = function () {
      fetch("/api/slice")
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.status !== "running" && data.next) {
            window.location = data.next;
            return;
          }
          if (data.status === "none") {
            window.location = "/project/send";
            return;
          }
          sliceTicks += 1;
          if (sliceTicks % 15 === 0 && data.text) {
            sliceLine.textContent = data.text;
          }
          setTimeout(slicePoll, 1000);
        })
        .catch(function () {
          sliceLine.textContent =
            "Lost contact with AccessSlice - is the program still " +
            "running?";
        });
    };

    setTimeout(slicePoll, 1000);
    return;
  }

  /* ------------------------------------------- waiting-screen poll */

  var line = document.getElementById("watch-line");
  if (!line) return;

  var ticks = 0;

  function poll() {
    fetch("/api/watch")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.found) {
          // Never open the project uninvited (Edis, 2026-08-26): the
          // page asks instead. The confirm link is revealed and
          // focused, so Enter says yes, and the manual-open and cancel
          // links right below are the other answers. No auto-navigation
          // - an earlier version navigated by itself and that was
          // explicitly unwanted.
          line.textContent =
            "Found " + data.name + " - do you want to open it in " +
            "AccessSlice? Choose below.";
          var link = document.getElementById("open-found-link");
          if (link) {
            link.textContent = "Yes, open " + data.name;
            link.hidden = false;
            link.focus();
          }
          return;
        }
        ticks += 1;
        // A periodic heartbeat, but not constant live-region chatter.
        if (ticks % 20 === 0) {
          line.textContent =
            "Still watching the projects folder. Nothing saved yet.";
        }
        setTimeout(poll, 1000);
      })
      .catch(function () {
        line.textContent =
          "Lost contact with AccessSlice - is the program still running?";
      });
  }

  setTimeout(poll, 1000);
})();
