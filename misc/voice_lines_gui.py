"""Tkinter GUI: list every spoken dialogue line in a plugin with its speaker and voice file, play existing audio,
record missing lines from the microphone, and pack the plugin with its voice files into a zip or the Data folder.

Recordings are saved as WAV to <plugin folder>\\Sound\\Voice\\<plugin>\\<voice type>\\<quest>_<topic>_<info id>_<n>.wav,
which Skyrim SE plays without a .lip file (the face stays still). Playing .fuz/.xwm files needs ffmpeg on PATH.

Usage: python misc/voice_lines_gui.py [plugin.esp]"""
import os
import queue
import sys
import threading
import tkinter as tk
import winsound
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voice_lines_model import (EMOTIONS, Recorder, all_idles, edit_response, all_voice_types, assign_voice_type, bsa_index, convert_to_fuz, create_voice_type, find_audio,
                               find_voice_tools,
                               import_audio, line_wav_path, load_lines, playable_wav)
from tools.core.plugin_edit import package_plugin
from tools.records.maintenance import install

DATA_DIR = Path(os.environ.get('SKYRIM_DATA_DIR',
                               r"C:\Program Files (x86)\Steam\steamapps\common\Skyrim Special Edition\Data"))


class App(tk.Tk):
    def __init__(self, plugin=None):
        super().__init__()
        self.title("Skyrim voice line editor")
        self.geometry("1300x700")
        self.plugin = None
        self.model = None
        self.lines = []
        self.bsas = {}
        self.vtypes = []
        self.recorder = None
        self.recording = None
        self.pending_voice = None
        self.calls = queue.Queue()
        self._build_ui()
        self.after(100, self.poll)
        if plugin:
            self.load(plugin)

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill='x', padx=6, pady=4)
        ttk.Button(top, text="Open plugin...", command=self.choose).pack(side='left')
        ttk.Button(top, text="Pack zip", command=self.pack_zip).pack(side='left', padx=(10, 0))
        ttk.Button(top, text="Install to Data", command=self.install_data).pack(side='left', padx=4)
        self.status = ttk.Label(top, text="")
        self.status.pack(side='left', padx=10)

        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=6)
        self.filter = tk.StringVar()
        self.filter.trace_add('write', lambda *_: self.fill())
        ttk.Label(bar, text="Filter:").pack(side='left')
        ttk.Entry(bar, textvariable=self.filter, width=40).pack(side='left', padx=4)
        self.only_missing = tk.BooleanVar()
        ttk.Checkbutton(bar, text="Missing audio only", variable=self.only_missing,
                        command=self.fill).pack(side='left', padx=8)

        cols = ('quest', 'topic', 'speaker', 'voice', 'audio', 'text')
        self.tree = ttk.Treeview(self, columns=cols, show='headings', selectmode='browse')
        for c, w in zip(cols, (150, 170, 160, 130, 70, 600)):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w, stretch=(c == 'text'))
        sb = ttk.Scrollbar(self, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side='right', fill='y')
        self.tree.pack(fill='both', expand=True, padx=6, pady=4)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        self.tree.bind('<Double-1>', lambda e: self.play())

        panel = ttk.Frame(self)
        panel.pack(fill='x', side='bottom', padx=6, pady=6)
        self.detail = tk.Text(panel, height=4, wrap='word')
        self.detail.pack(fill='x', pady=(0, 4))
        ed = ttk.LabelFrame(panel, text="Edit response")
        ed.pack(fill='x', pady=(0, 4))
        self.edit_text = tk.Text(ed, height=3, wrap='word')
        self.edit_text.pack(fill='x', padx=4, pady=4)
        er = ttk.Frame(ed)
        er.pack(fill='x', padx=4, pady=(0, 4))
        self.emotion = tk.StringVar()
        self.emo_value = tk.IntVar(value=50)
        self.emo_anim = tk.BooleanVar()
        self.spk_idle = tk.StringVar()
        self.lis_idle = tk.StringVar()
        ttk.Label(er, text="Emotion:").pack(side='left')
        ttk.Combobox(er, textvariable=self.emotion, values=EMOTIONS, width=10, state='readonly').pack(side='left', padx=4)
        ttk.Spinbox(er, from_=0, to=100, textvariable=self.emo_value, width=5).pack(side='left')
        ttk.Checkbutton(er, text="Use emotion animation", variable=self.emo_anim).pack(side='left', padx=8)
        ttk.Label(er, text="Speaker animation:").pack(side='left')
        self.spk_box = ttk.Combobox(er, textvariable=self.spk_idle, width=24)
        self.spk_box.pack(side='left', padx=4)
        ttk.Label(er, text="Listener:").pack(side='left')
        self.lis_box = ttk.Combobox(er, textvariable=self.lis_idle, width=24)
        self.lis_box.pack(side='left', padx=4)
        ttk.Button(er, text="Apply", command=self.apply_edit).pack(side='left', padx=4)
        row = ttk.Frame(panel)
        row.pack(fill='x')
        ttk.Label(row, text="Voice type:").pack(side='left')
        self.voice = tk.StringVar()
        self.voice_box = ttk.Combobox(row, textvariable=self.voice, width=28)
        self.voice_box.pack(side='left', padx=4)
        self.voice_box.bind('<<ComboboxSelected>>', lambda e: self.on_voice())
        self.voice_box.bind('<Return>', lambda e: self.on_voice())
        ttk.Button(row, text="New voice type...", command=self.new_voice_type).pack(side='left')
        ttk.Button(row, text="Play", command=self.play).pack(side='left', padx=(10, 0))
        ttk.Button(row, text="Stop", command=lambda: winsound.PlaySound(None, winsound.SND_PURGE)).pack(side='left', padx=4)
        self.rec_btn = ttk.Button(row, text="Record", command=self.toggle_record)
        self.rec_btn.pack(side='left', padx=(10, 0))
        ttk.Button(row, text="Upload audio...", command=self.upload).pack(side='left', padx=4)
        ttk.Button(row, text="To .fuz", command=lambda: self.to_fuz(False)).pack(side='left', padx=4)
        ttk.Button(row, text="All WAVs to .fuz", command=lambda: self.to_fuz(True)).pack(side='left')
        ttk.Button(row, text="Delete recording", command=self.delete).pack(side='left', padx=4)

    def choose(self):
        p = filedialog.askopenfilename(title="Plugin", filetypes=[("Plugins", "*.esp *.esm *.esl"), ("All", "*.*")])
        if p:
            self.load(p)

    def load(self, path):
        self.plugin = Path(path)
        self.status.config(text=f"Loading {self.plugin.name}...")

        def work():
            try:
                model, lines = load_lines(self.plugin, lambda s: self.calls.put(lambda: self.status.config(text=s)),
                                           master_dirs=[DATA_DIR])
                dirs = [DATA_DIR, self.plugin.parent] if DATA_DIR.is_dir() else [self.plugin.parent]
                bsas = bsa_index(dirs, self.plugin.name)
                for ln in lines:
                    find_audio(ln, self.plugin, [DATA_DIR] if DATA_DIR.is_dir() else (), bsas)
                self.calls.put(lambda: self.loaded(model, lines, bsas))
            except Exception as e:
                self.calls.put(lambda: (self.status.config(text="Load failed"), messagebox.showerror("Load failed", str(e))))
        threading.Thread(target=work, daemon=True).start()

    def loaded(self, model, lines, bsas):
        self.model, self.lines, self.bsas = model, lines, bsas
        self.vtypes = all_voice_types(model)
        self.voice_box['values'] = self.vtypes
        self.idles = all_idles(model)
        self.idle_names = {v: k for k, v in self.idles.items()}
        names = [''] + sorted(self.idles, key=str.lower)
        self.spk_box['values'] = self.lis_box['values'] = names
        if self.pending_voice:
            self.voice.set(self.pending_voice)
            self.pending_voice = None
        self.fill()

    def poll(self):
        while not self.calls.empty():
            self.calls.get()()
        self.after(100, self.poll)

    def has_audio(self, ln):
        return ln.audio is not None or ln.bsa is not None

    def fill(self):
        self.tree.delete(*self.tree.get_children())
        q = self.filter.get().lower()
        shown = 0
        for i, ln in enumerate(self.lines):
            if self.only_missing.get() and self.has_audio(ln):
                continue
            hay = f"{ln.quest} {ln.topic} {ln.speaker} {ln.text} {ln.prompt}".lower()
            if q and q not in hay:
                continue
            self.tree.insert('', 'end', iid=str(i), values=(
                ln.quest, ln.topic, ln.speaker, ', '.join(ln.voice_types), 'yes' if self.has_audio(ln) else 'MISSING', ln.text))
            shown += 1
        have = sum(self.has_audio(ln) for ln in self.lines)
        self.status.config(text=f"{self.plugin.name if self.plugin else ''}: {len(self.lines)} lines, "
                                f"{have} with audio, {shown} shown")

    def current(self):
        sel = self.tree.selection()
        return self.lines[int(sel[0])] if sel else None

    def on_select(self, _=None):
        ln = self.current()
        if ln is None:
            return
        self.voice.set(ln.voice_types[0] if ln.voice_types else '')
        self.detail.delete('1.0', 'end')
        where = ln.audio or (f"{ln.bsa[0]} :: {ln.bsa[1]}" if ln.bsa else 'no audio')
        prompt = f"Player prompt: {ln.prompt}\n" if ln.prompt else ''
        self.detail.insert('1.0', f"{ln.speaker}: {ln.text}\n{prompt}File: {ln.stem}  ({where})")
        self.edit_text.delete('1.0', 'end')
        self.edit_text.insert('1.0', ln.text)
        self.emotion.set(EMOTIONS[ln.emotion] if ln.emotion < len(EMOTIONS) else EMOTIONS[0])
        self.emo_value.set(ln.emotion_value)
        self.emo_anim.set(ln.use_emotion_anim)
        names = getattr(self, 'idle_names', {})
        self.spk_idle.set(names.get(ln.speaker_idle, ''))
        self.lis_idle.set(names.get(ln.listener_idle, ''))

    def apply_edit(self):
        ln = self.current()
        if ln is None:
            return
        text = self.edit_text.get('1.0', 'end').strip()
        if not text:
            messagebox.showerror("Edit", "The response text can't be empty.")
            return
        if text != ln.text and self.has_audio(ln) and not messagebox.askyesno(
                "Edit", "This line has audio. Changing the text leaves the voice and lip sync saying the old words "
                        "until you re-record or re-convert it.\n\nApply anyway?"):
            return
        try:
            idles = [self.idles.get(v.get().strip(), 0) if v.get().strip() else 0 for v in (self.spk_idle, self.lis_idle)]
            if any(v.get().strip() and not i for v, i in zip((self.spk_idle, self.lis_idle), idles)):
                raise ValueError("unknown animation name; pick one from the list or leave it blank")
            edit_response(self.model, self.plugin, ln, text, EMOTIONS.index(self.emotion.get()),
                          int(self.emo_value.get()), self.emo_anim.get(), *idles)
        except Exception as e:
            messagebox.showerror("Edit failed", str(e))
            return
        self.refresh(ln)
        self.status.config(text="Saved (snapshot 'pre_dialogue_edit' taken first)")

    def on_voice(self):
        ln = self.current()
        if ln and self.voice.get() and self.voice.get() not in ln.voice_types:
            ln.voice_types.insert(0, self.voice.get())
        if ln:
            find_audio(ln, self.plugin, [DATA_DIR] if DATA_DIR.is_dir() else (), self.bsas, self.voice.get())
            self.refresh(ln)

    def refresh(self, ln):
        iid = str(self.lines.index(ln))
        if self.tree.exists(iid):
            self.tree.set(iid, 'audio', 'yes' if self.has_audio(ln) else 'MISSING')
            self.tree.set(iid, 'voice', ', '.join(ln.voice_types))
            self.tree.set(iid, 'text', ln.text)
        self.on_select()

    def play(self):
        ln = self.current()
        if ln is None:
            return
        try:
            wav = playable_wav(ln)
        except Exception as e:
            messagebox.showerror("Playback", str(e))
            return
        if wav is None:
            messagebox.showinfo("Playback", "This line has no audio yet. Use Record.")
            return
        winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)

    def toggle_record(self):
        if self.recording is None:
            ln = self.current()
            vt = self.voice.get().strip()
            if ln is None:
                return
            if not vt:
                messagebox.showinfo("Record", "Pick a voice type first; the voice file folder is named after it.")
                return
            if self.has_audio(ln) and not messagebox.askyesno("Record", "This line already has audio. Record a new .wav?"):
                return
            try:
                self.recorder = Recorder()
                self.recorder.start()
            except Exception as e:
                messagebox.showerror("Record", f"Could not start recording: {e}")
                return
            self.recording = (ln, vt)
            self.rec_btn.config(text="Stop recording")
            self.status.config(text="Recording... " + ln.text[:80])
        else:
            ln, vt = self.recording
            dest = line_wav_path(ln, self.plugin, vt)
            try:
                self.recorder.stop(dest)
            except Exception as e:
                messagebox.showerror("Record", str(e))
            self.recording = None
            self.rec_btn.config(text="Record")
            if vt not in ln.voice_types:
                ln.voice_types.insert(0, vt)
            find_audio(ln, self.plugin, [DATA_DIR] if DATA_DIR.is_dir() else (), self.bsas, vt)
            self.refresh(ln)
            self.fill()

    def reload_keep(self):
        """Re-read the plugin after it was edited (new voice type or NPC change)."""
        self.load(self.plugin)

    def new_voice_type(self):
        if self.plugin is None:
            return
        ln = self.current()
        edid = simpledialog.askstring("New voice type", "Editor ID (letters, digits, underscore):",
                                      initialvalue=(ln.speaker.split(',')[0].replace(' ', '') + 'Voice') if ln else '')
        if not edid:
            return
        female = messagebox.askyesno("New voice type", "Is this a female voice type?")
        try:
            made = create_voice_type(self.plugin, edid.strip(), female)
        except Exception as e:
            messagebox.showerror("New voice type", str(e))
            return
        npcs = [f for f in (ln.npcs if ln else []) if self.model.get(f) is not None
                and self.model.get(f).plugin.file_path.name.lower() == self.plugin.name.lower()]
        note = ''
        if npcs and messagebox.askyesno("Assign", "Also set this voice type on this line's speaker(s) in the plugin?"):
            try:
                done = assign_voice_type(self.plugin, {self.model.get(f).form_id.value for f in npcs}, made['form_id'])
                note = f"\nAssigned to: {', '.join(done)}"
            except Exception as e:
                messagebox.showerror("Assign", str(e))
        messagebox.showinfo("New voice type", f"Created {edid}.{note}")
        self.pending_voice = edid.strip()
        self.reload_keep()

    def upload(self):
        ln = self.current()
        if ln is None:
            return
        vt = self.voice.get().strip()
        if not vt:
            messagebox.showinfo("Upload", "Pick a voice type first; the voice file folder is named after it.")
            return
        src = filedialog.askopenfilename(title=f"Audio for {ln.stem}", filetypes=[
            ("Audio", "*.wav *.fuz *.xwm *.mp3 *.ogg *.flac *.m4a"), ("All", "*.*")])
        if not src:
            return
        if self.has_audio(ln) and not messagebox.askyesno("Upload", "This line already has audio. Replace it?"):
            return
        try:
            import_audio(ln, src, self.plugin, vt)
        except Exception as e:
            messagebox.showerror("Upload", str(e))
            return
        if vt not in ln.voice_types:
            ln.voice_types.insert(0, vt)
        find_audio(ln, self.plugin, [DATA_DIR] if DATA_DIR.is_dir() else (), self.bsas, vt)
        self.refresh(ln)
        self.fill()

    def to_fuz(self, everything):
        """Encode loose WAV recordings to .fuz (xWMA + generated lip sync) with the Skyrim SE Tools."""
        tools = find_voice_tools(DATA_DIR)
        if len(tools) < 3:
            messagebox.showerror(".fuz", "Needs xwmaencode, LipGenerator and LIPFuzer from the Skyrim SE Tools "
                                 "folder (next to Data, or set SKYRIM_TOOLS_DIR).")
            return
        if everything:
            todo = [ln for ln in self.lines if ln.audio is not None and ln.audio.suffix.lower() == '.wav'
                    and self.plugin.parent in ln.audio.parents]
        else:
            ln = self.current()
            todo = [ln] if ln and ln.audio is not None and ln.audio.suffix.lower() == '.wav' else []
        if not todo:
            messagebox.showinfo(".fuz", "No loose .wav recordings to convert.")
            return

        def work():
            errors = []
            for i, ln in enumerate(todo, 1):
                self.calls.put(lambda i=i: self.status.config(text=f"Converting {i}/{len(todo)}..."))
                try:
                    convert_to_fuz(ln, self.plugin, tools)
                except Exception as e:
                    errors.append(f"{ln.stem}: {e}")
            def done():
                self.fill()
                self.on_select()
                if errors:
                    messagebox.showerror(".fuz", f"{len(errors)} failed:\n" + "\n".join(errors[:5]))
                else:
                    messagebox.showinfo(".fuz", f"Converted {len(todo)} line(s) to .fuz")
            self.calls.put(done)
        threading.Thread(target=work, daemon=True).start()

    def delete(self):
        ln = self.current()
        if ln is None or ln.audio is None or ln.audio.suffix.lower() != '.wav' \
                or self.plugin.parent not in ln.audio.parents:
            messagebox.showinfo("Delete", "Only .wav recordings in the plugin's output folder can be deleted here.")
            return
        if messagebox.askyesno("Delete", f"Delete {ln.audio.name}?"):
            ln.audio.unlink(missing_ok=True)
            find_audio(ln, self.plugin, [DATA_DIR] if DATA_DIR.is_dir() else (), self.bsas, self.voice.get() or None)
            self.refresh(ln)
            self.fill()

    def pack_zip(self):
        if self.plugin is None:
            return
        try:
            r = package_plugin(self.plugin)
        except Exception as e:
            messagebox.showerror("Pack", str(e))
            return
        voices = [f for f in r['files'] if f.lower().startswith('sound/')]
        messagebox.showinfo("Pack", f"{r['zip']}\n{len(r['files'])} files ({len(voices)} voice files)")

    def install_data(self):
        if self.plugin is None:
            return
        if not messagebox.askyesno("Install", f"Copy the plugin and its files into\n{DATA_DIR}?"):
            return
        try:
            r = install(self.plugin, DATA_DIR)
        except Exception as e:
            messagebox.showerror("Install", str(e))
            return
        messagebox.showinfo("Install", r['summary'])


if __name__ == '__main__':
    App(sys.argv[1] if len(sys.argv) > 1 else None).mainloop()
