/* app.js - behavior of the SMdownloader page. Talks to the Flask API in SMdownloader.py. */
(function () {
    'use strict';

    var el = function (id) { return document.getElementById(id); };
    var lastJson = '';
    var busy = false;
    var pickers = {};   // job id -> picker element, or 'loading' while its list is being fetched
    var videoInfoUrl = '';
    var videoDuration = 0;
    var playlistOptionsUnavailable = false;

    function setMsg(text) {
        var m = el('msg');
        m.textContent = text || '';
        m.hidden = !text;
    }

    function updateNote() {
        var q = el('quality').value;
        el('quality-note').textContent =
            q === 'audio' ? 'Audio only, saved as MP3 (192 kbps).' :
            q === 'high' ? 'Best video and audio available, saved as MP4.' :
            'Up to ' + q + 'p, saved as MP4.';
    }

    function updateSubtitleToggle() {
        var isAudio = el('quality').value === 'audio';
        var checkbox = el('download-subtitles');
        checkbox.disabled = isAudio;
        if (isAudio) { checkbox.checked = false; }
    }

    function clearVideoInfo() {
        videoInfoUrl = '';
        videoDuration = 0;
        el('timeline-controls').hidden = true;
        el('time-range-status').textContent = 'Select a video to load its duration.';
    }

    function setPlaylistOptionsUnavailable(unavailable) {
        playlistOptionsUnavailable = unavailable;
        el('custom-title').disabled = unavailable;
        el('select-time').disabled = unavailable;
        el('playlist-options-note').hidden = !unavailable;
        if (unavailable) {
            el('time-range-fields').hidden = true;
            el('timeline-controls').hidden = true;
            el('select-time').setAttribute('aria-expanded', 'false');
            clearVideoInfo();
        }
    }

    el('url').addEventListener('input', clearVideoInfo);

    function formatTime(seconds) {
        var total = Math.max(0, Math.round(seconds));
        var hours = Math.floor(total / 3600);
        var minutes = Math.floor((total % 3600) / 60);
        var remainder = total % 60;
        var mm = (hours ? String(minutes).padStart(2, '0') : String(minutes));
        var ss = String(remainder).padStart(2, '0');
        return hours ? hours + ':' + mm + ':' + ss : mm + ':' + ss;
    }

    function parseTime(value) {
        var parts = value.trim().split(':');
        if (parts.length !== 2 && parts.length !== 3) { return null; }
        if (parts.some(function (part) { return !/^\d+$/.test(part); })) { return null; }
        var numbers = parts.map(Number);
        if (parts.length === 2) {
            if (numbers[1] >= 60) { return null; }
            return numbers[0] * 60 + numbers[1];
        }
        if (numbers[1] >= 60 || numbers[2] >= 60) { return null; }
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2];
    }

    function updateTimeline(changedHandle) {
        if (!videoDuration) { return; }
        var startInput = el('start-time');
        var endInput = el('end-time');
        var start = Number(startInput.value);
        var end = Number(endInput.value);
        if (changedHandle === 'start' && start >= end) {
            start = Math.max(0, end - 1);
            startInput.value = String(start);
        } else if (changedHandle === 'end' && end <= start) {
            end = Math.min(videoDuration, start + 1);
            endInput.value = String(end);
        }

        el('start-time-label').textContent = formatTime(start);
        el('end-time-label').textContent = formatTime(end);
        el('start-time-text').value = formatTime(start);
        el('end-time-text').value = formatTime(end);
        el('video-duration-label').textContent = formatTime(videoDuration);
        el('duration-scale').textContent = formatTime(videoDuration);
        var slider = el('timeline-slider');
        slider.style.setProperty('--range-start', (start * 100 / videoDuration) + '%');
        slider.style.setProperty('--range-end', (end * 100 / videoDuration) + '%');
    }

    function updateSliderFromManual(which) {
        var input = el(which === 'start' ? 'start-time-text' : 'end-time-text');
        var time = parseTime(input.value);
        var status = el('time-range-status');
        if (time === null) {
            status.textContent = 'Use M:SS or H:MM:SS.';
            status.hidden = false;
            return;
        }
        if (time > videoDuration) {
            status.textContent = 'Time cannot exceed the video duration.';
            status.hidden = false;
            return;
        }
        var otherTime = Number(el(which === 'start' ? 'end-time' : 'start-time').value);
        if ((which === 'start' && time >= otherTime) || (which === 'end' && time <= otherTime)) {
            status.textContent = 'End time must be after start time.';
            status.hidden = false;
            return;
        }

        el(which === 'start' ? 'start-time' : 'end-time').value = String(time);
        status.hidden = true;
        updateTimeline(which);
    }

    async function loadVideoDuration(url) {
        var status = el('time-range-status');
        var controls = el('timeline-controls');
        if (videoInfoUrl === url && videoDuration) {
            controls.hidden = false;
            status.hidden = true;
            return;
        }

        controls.hidden = true;
        status.hidden = false;
        status.textContent = 'Getting video duration...';
        el('select-time').disabled = true;
        try {
            var info = await api('/api/video-info', {
                method: 'POST',
                body: JSON.stringify({ url: url })
            });
            videoDuration = Math.floor(info.duration);
            videoInfoUrl = url;
            if (videoDuration < 2) { throw new Error('Video is too short to select a range.'); }
            el('start-time').min = '0';
            el('start-time').max = String(videoDuration - 1);
            el('start-time').value = '0';
            el('end-time').min = '1';
            el('end-time').max = String(videoDuration);
            el('end-time').value = String(videoDuration);
            updateTimeline();
            status.hidden = true;
            controls.hidden = false;
        } catch (err) {
            videoInfoUrl = '';
            videoDuration = 0;
            status.textContent = err.message;
        } finally {
            el('select-time').disabled = playlistOptionsUnavailable;
        }
    }

    el('paste-url').addEventListener('click', async function () {
        try {
            var clipboardText = await navigator.clipboard.readText();
            if (!clipboardText.trim()) {
                setMsg('Clipboard is empty.');
                return;
            }
            el('url').value = clipboardText.trim();
            clearVideoInfo();
            el('url').focus();
            setMsg('');
        } catch (err) {
            setMsg('Clipboard access is unavailable. Paste the URL into the field manually.');
        }
    });

    async function api(path, options) {
        var opts = options || {};
        var res = await fetch(path, {
            method: opts.method || 'GET',
            body: opts.body,
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'sm-downloader' }
        });
        var body = null;
        try { body = await res.json(); } catch (e) { /* no JSON body */ }
        if (!res.ok) {
            throw new Error((body && body.error) || ('Request failed (' + res.status + ').'));
        }
        return body;
    }

    // ---- formatting ----
    function fmtBytes(n) {
        if (typeof n !== 'number' || !isFinite(n) || n < 0) { return ''; }
        var units = ['B', 'KB', 'MB', 'GB', 'TB'];
        var i = 0;
        var v = n;
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
        return (i === 0 ? String(Math.round(v)) : v.toFixed(v >= 100 ? 0 : 1)) + ' ' + units[i];
    }

    function fmtSpeed(bps) {
        return bps ? fmtBytes(bps) + '/s' : '';
    }

    function fmtEta(s) {
        if (s === null || s === undefined) { return ''; }
        var total = Math.round(s);
        var m = Math.floor(total / 60);
        var r = total % 60;
        return m + ':' + (r < 10 ? '0' : '') + r;
    }

    function fmtDuration(sec) {
        if (typeof sec !== 'number' || !isFinite(sec) || sec <= 0) { return ''; }
        var total = Math.round(sec);
        var h = Math.floor(total / 3600);
        var m = Math.floor((total % 3600) / 60);
        var s = total % 60;
        var ss = (s < 10 ? '0' : '') + s;
        return h > 0 ? h + ':' + (m < 10 ? '0' : '') + m + ':' + ss : m + ':' + ss;
    }

    function describe(job) {
        var parts = [];
        var showQualityAndSubtitles = job.platform === 'YouTube' || job.platform === 'Facebook';
        if (job.quality === 'audio') { parts.push('MP3'); }
        else if (showQualityAndSubtitles && job.quality === 'high') { parts.push('MP4 · best quality'); }
        else if (showQualityAndSubtitles) { parts.push('MP4 · up to ' + job.quality + 'p'); }
        else { parts.push('MP4'); }
        var platform = job.platform;
        if (!platform) {
            try {
                platform = new URL(job.url).hostname;
                if (platform === 'lnkd.in' || platform.endsWith('.lnkd.in')) {
                    platform = 'LinkedIn';
                }
            } catch (err) {
                platform = 'Other';
            }
        }
        parts.push(platform);
        if (showQualityAndSubtitles && job.download_subtitles) { parts.push('English subtitles'); }
        return parts.join(' · ');
    }

    function statusLine(job) {
        switch (job.status) {
            case 'inspecting': return 'Checking link...';
            case 'queued': return 'Waiting in queue';
            case 'downloading':
                var bits = [];
                if (job.size_total) {
                    bits.push('Downloading ' + Math.round(job.percent) + '%');
                    bits.push(fmtBytes(job.size_done) + ' of ' + fmtBytes(job.size_total));
                } else if (job.size_done) {
                    bits.push('Downloading ' + fmtBytes(job.size_done));   // total size unknown
                } else {
                    bits.push('Starting...');
                }
                var sp = fmtSpeed(job.speed);
                var eta = fmtEta(job.eta);
                if (sp) { bits.push(sp); }
                if (eta) { bits.push('ETA ' + eta); }
                return bits.join(' · ');
            case 'paused':
                if (job.resume_requested) { return 'Waiting for a download slot'; }
                return 'Paused' + (job.size_done ? ' · ' + fmtBytes(job.size_done) + ' downloaded' : '');
            case 'processing': return 'Converting...';
            case 'done': return 'Finished';
            case 'cancelled': return 'Cancelled';
            case 'error': return 'Failed';
            default: return job.status;
        }
    }

    // ---- queue ----
    function render(jobs) {
        // playlists waiting for a selection live in the picker panels, not in the queue
        var visible = jobs.filter(function (j) { return j.status !== 'choosing'; });
        var list = el('jobs');
        list.textContent = '';
        el('empty').hidden = visible.length > 0;

        visible.forEach(function (job) {
            var li = document.createElement('li');
            li.className = 'job' + (job.status === 'error' ? ' is-error' : '');

            // Titles, URLs and errors come from remote sites: always textContent, never innerHTML.
            var title = document.createElement('div');
            title.className = 'job-title';
            var videoLink = document.createElement('a');
            videoLink.href = job.url;
            videoLink.target = '_blank';
            videoLink.rel = 'noopener noreferrer';
            videoLink.textContent = job.custom_title || job.title || job.url;
            title.appendChild(videoLink);
            li.appendChild(title);

            var meta = document.createElement('div');
            meta.className = 'job-meta';
            meta.appendChild(document.createTextNode(describe(job) + ' · '));
            var status = document.createElement('span');
            status.className = 'job-status job-status-' + job.status;
            status.textContent = statusLine(job);
            meta.appendChild(status);
            if (job.status === 'done' && job.size_total) {
                meta.appendChild(document.createTextNode(' · ' + fmtBytes(job.size_total)));
            }
            li.appendChild(meta);

            var showBar = job.status === 'processing' || job.status === 'done' ||
                          (job.status === 'downloading' && job.size_total);
            if (showBar) {
                var track = document.createElement('div');
                track.className = 'track';
                track.setAttribute('role', 'progressbar');
                track.setAttribute('aria-valuemin', '0');
                track.setAttribute('aria-valuemax', '100');
                track.setAttribute('aria-valuenow', String(Math.round(job.percent)));
                var fill = document.createElement('div');
                fill.className = 'fill';
                fill.style.width = Math.max(0, Math.min(100, job.percent)) + '%';
                track.appendChild(fill);
                li.appendChild(track);
            }

            if (job.error) {
                var err = document.createElement('div');
                err.className = 'job-error';
                err.textContent = job.error;
                li.appendChild(err);
            }

            var actions = document.createElement('div');
            actions.className = 'job-actions';

            if (job.status === 'inspecting' || job.status === 'queued' ||
                job.status === 'downloading' || job.status === 'paused' ||
                job.status === 'error' || job.status === 'done') {
                var editName = document.createElement('button');
                editName.type = 'button';
                editName.className = 'action secondary small';
                editName.textContent = 'Edit name';
                editName.addEventListener('click', function () { editJobName(job); });
                actions.appendChild(editName);
            }

            if (job.status === 'error') {
                var retry = document.createElement('button');
                retry.type = 'button';
                retry.className = 'action secondary small';
                retry.textContent = 'Retry';
                retry.addEventListener('click', function () { retryJob(job.id); });
                actions.appendChild(retry);

                if (job.download_subtitles && /subtitle/i.test(job.error || '')) {
                    var skipSubtitles = document.createElement('button');
                    skipSubtitles.type = 'button';
                    skipSubtitles.className = 'action secondary small';
                    skipSubtitles.textContent = 'Skip subtitles';
                    skipSubtitles.addEventListener('click', function () { retryWithoutSubtitles(job.id); });
                    actions.appendChild(skipSubtitles);
                }
            }

            if (job.status === 'downloading' || job.status === 'paused') {
                var pause = document.createElement('button');
                pause.type = 'button';
                pause.className = 'action secondary small';
                pause.textContent = job.status === 'paused' ? 'Resume' : 'Pause';
                pause.addEventListener('click', function () { togglePause(job.id); });
                actions.appendChild(pause);
            }

            if (job.status === 'done' && job.has_file) {
                var open = document.createElement('a');
                open.className = 'action secondary small';
                open.href = '/api/jobs/' + encodeURIComponent(job.id) + '/open';
                open.target = '_blank';
                open.rel = 'noopener';
                open.textContent = 'Open';
                actions.appendChild(open);

                var move = document.createElement('button');
                move.type = 'button';
                move.className = 'action secondary small';
                move.textContent = 'Move to';
                move.addEventListener('click', function () { moveJobFile(job.id); });
                actions.appendChild(move);
            }

            if (job.status !== 'processing') {
                var active = job.status === 'inspecting' || job.status === 'queued' ||
                             job.status === 'downloading' || job.status === 'paused';
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'action secondary small';
                btn.textContent = active ? 'Cancel' : 'Remove';
                btn.addEventListener('click', function () { removeJob(job.id); });
                actions.appendChild(btn);
            }

            li.appendChild(actions);
            list.appendChild(li);
        });
    }

    async function removeJob(id) {
        try {
            await api('/api/jobs/' + encodeURIComponent(id), { method: 'DELETE' });
        } catch (err) {
            setMsg(err.message);
        }
        refresh();
    }

    async function editJobName(job) {
        var name = window.prompt('Edit video name', job.custom_title || job.title || '');
        if (name === null) { return; }
        try {
            await api('/api/jobs/' + encodeURIComponent(job.id) + '/name', {
                method: 'POST',
                body: JSON.stringify({ name: name })
            });
            setMsg('');
            refresh();
        } catch (err) {
            setMsg(err.message);
        }
    }

    async function moveJobFile(id) {
        try {
            var result = await api('/api/jobs/' + encodeURIComponent(id) + '/move', {
                method: 'POST',
                body: '{}'
            });
            if (!result.cancelled) {
                setMsg('Moved "' + result.file_name + '" to the selected folder.');
                refresh();
            }
        } catch (err) {
            setMsg(err.message);
        }
    }

    async function retryJob(id) {
        try {
            await api('/api/jobs/' + encodeURIComponent(id) + '/retry', { method: 'POST', body: '{}' });
        } catch (err) {
            setMsg(err.message);
        }
        refresh();
    }

    async function retryWithoutSubtitles(id) {
        try {
            await api('/api/jobs/' + encodeURIComponent(id) + '/skip-subtitles', { method: 'POST', body: '{}' });
        } catch (err) {
            setMsg(err.message);
        }
        refresh();
    }

    async function togglePause(id) {
        try {
            await api('/api/jobs/' + encodeURIComponent(id) + '/pause', { method: 'POST', body: '{}' });
        } catch (err) {
            setMsg(err.message);
        }
        refresh();
    }

    // ---- playlist pickers (built once per playlist, never re-rendered, so checkbox state survives polling) ----
    function removePicker(id) {
        var node = pickers[id];
        if (node && node !== 'loading' && node.parentNode) { node.parentNode.removeChild(node); }
        delete pickers[id];
    }

    function syncPickers(jobs) {
        var waiting = {};
        jobs.forEach(function (j) { if (j.status === 'choosing') { waiting[j.id] = true; } });
        setPlaylistOptionsUnavailable(Object.keys(waiting).length > 0);
        Object.keys(pickers).forEach(function (id) { if (!waiting[id]) { removePicker(id); } });
        Object.keys(waiting).forEach(function (id) { if (!pickers[id]) { buildPicker(id); } });
    }

    function button(text, className, onClick) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = className;
        b.textContent = text;
        b.addEventListener('click', onClick);
        return b;
    }

    async function buildPicker(id) {
        pickers[id] = 'loading';
        var data;
        try {
            data = await api('/api/jobs/' + encodeURIComponent(id) + '/entries');
        } catch (err) {
            delete pickers[id];   // tried again on the next poll while the playlist is still waiting
            return;
        }
        if (pickers[id] !== 'loading') { return; }   // dismissed while loading

        var box = document.createElement('section');
        box.className = 'panel picker';

        var head = document.createElement('div');
        head.className = 'picker-head';
        var h2 = document.createElement('h2');
        h2.textContent = 'Choose videos';
        var count = document.createElement('span');
        count.className = 'picker-count';
        count.setAttribute('aria-live', 'polite');
        head.appendChild(h2);
        head.appendChild(count);
        box.appendChild(head);

        var name = document.createElement('p');
        name.className = 'picker-title';
        name.textContent = data.title || 'Playlist';
        box.appendChild(name);

        var folderName = data.title || 'Playlist';
        var folderRow = document.createElement('div');
        folderRow.className = 'playlist-folder';
        var folderLabel = document.createElement('span');
        folderLabel.textContent = 'Save selected videos in: ';
        var folderValue = document.createElement('strong');
        folderValue.textContent = folderName;
        folderRow.appendChild(folderLabel);
        folderRow.appendChild(folderValue);
        folderRow.appendChild(button('Edit folder name', 'action secondary small', function () {
            var editedName = window.prompt('Playlist folder name', folderName);
            if (editedName === null) { return; }
            editedName = editedName.trim();
            if (!editedName) { return; }
            folderName = editedName;
            folderValue.textContent = folderName;
        }));
        box.appendChild(folderRow);

        var boxes = [];
        function update() {
            var n = boxes.filter(function (b) { return b.checked; }).length;
            count.textContent = n + ' of ' + boxes.length + ' selected';
            go.textContent = 'Download ' + n + ' selected';
            go.disabled = n === 0;
        }
        function setAll(value) {
            boxes.forEach(function (b) { b.checked = value; });
            update();
        }

        var tools = document.createElement('div');
        tools.className = 'picker-tools';
        tools.appendChild(button('Select all', 'action secondary small', function () { setAll(true); }));
        tools.appendChild(button('Select none', 'action secondary small', function () { setAll(false); }));
        box.appendChild(tools);

        var ul = document.createElement('ul');
        ul.className = 'picker-list';
        data.entries.forEach(function (entry) {
            var li = document.createElement('li');
            var label = document.createElement('label');
            label.className = 'pick';
            var cb = document.createElement('input');
            cb.type = 'checkbox';
            cb.checked = true;                     // everything starts selected
            cb.value = String(entry.n);
            cb.addEventListener('change', update);
            boxes.push(cb);
            var num = document.createElement('span');
            num.className = 'pick-num';
            num.textContent = String(entry.n);
            var t = document.createElement('span');
            t.className = 'pick-title';
            t.textContent = entry.title;
            var dur = document.createElement('span');
            dur.className = 'pick-dur';
            dur.textContent = fmtDuration(entry.duration);
            label.appendChild(cb);
            label.appendChild(num);
            label.appendChild(t);
            label.appendChild(dur);
            li.appendChild(label);
            ul.appendChild(li);
        });
        box.appendChild(ul);

        if (data.truncated) {
            var cut = document.createElement('p');
            cut.className = 'note';
            cut.textContent = 'Only the first ' + data.limit + ' videos are listed.';
            box.appendChild(cut);
        }

        var msg = document.createElement('p');
        msg.className = 'msg';
        msg.hidden = true;
        msg.setAttribute('role', 'alert');
        box.appendChild(msg);

        var actions = document.createElement('div');
        actions.className = 'picker-actions';
        var go = button('', 'action', async function () {
            var chosen = boxes.filter(function (b) { return b.checked; }).map(function (b) { return Number(b.value); });
            if (!chosen.length) { return; }
            go.disabled = true;
            try {
                await api('/api/jobs/' + encodeURIComponent(id) + '/select', {
                    method: 'POST',
                    body: JSON.stringify({ indexes: chosen, folder_name: folderName })
                });
                removePicker(id);
            } catch (err) {
                msg.textContent = err.message;
                msg.hidden = false;
                update();
            }
            refresh();
        });
        actions.appendChild(go);
        actions.appendChild(button('Cancel', 'action secondary', async function () {
            try { await api('/api/jobs/' + encodeURIComponent(id), { method: 'DELETE' }); } catch (err) { /* already gone */ }
            removePicker(id);
            refresh();
        }));
        box.appendChild(actions);

        update();
        el('pickers').appendChild(box);
        pickers[id] = box;
    }

    // ---- polling ----
    async function refresh() {
        if (busy) { return; }
        busy = true;
        try {
            var jobs = await api('/api/jobs');
            el('conn').hidden = true;
            var json = JSON.stringify(jobs);
            if (json !== lastJson) {
                lastJson = json;
                render(jobs);
            }
            syncPickers(jobs);
        } catch (err) {
            el('conn').hidden = false;
        } finally {
            busy = false;
        }
    }

    el('add-form').addEventListener('submit', async function (e) {
        e.preventDefault();
        var url = el('url').value.trim();
        if (!url) { setMsg('Paste a link first.'); return; }
        setMsg('');
        var payload = {
            url: url,
            quality: el('quality').value,
            download_subtitles: !!el('download-subtitles').checked,
            custom_title: el('custom-title').value.trim()
        };
        if (videoInfoUrl === url && videoDuration) {
            var start = Number(el('start-time').value);
            var end = Number(el('end-time').value);
            var manualStart = parseTime(el('start-time-text').value);
            var manualEnd = parseTime(el('end-time-text').value);
            if (manualStart === null || manualEnd === null || manualStart >= manualEnd || manualEnd > videoDuration) {
                setMsg('Enter valid start and end times within the video duration.');
                el('time-range-fields').hidden = false;
                el(manualStart === null || manualStart >= manualEnd ? 'start-time-text' : 'end-time-text').focus();
                return;
            }
            start = manualStart;
            end = manualEnd;
            if (start > 0 || end < videoDuration) {
                payload.start_time = formatTime(start);
                payload.end_time = formatTime(end);
            }
        }
        try {
            await api('/api/jobs', {
                method: 'POST',
                body: JSON.stringify(payload)
            });
            el('url').value = '';
            el('custom-title').value = '';
            videoInfoUrl = '';
            videoDuration = 0;
            el('timeline-controls').hidden = true;
            el('time-range-status').textContent = 'Select a video to load its duration.';
            el('time-range-fields').hidden = true;
            el('select-time').setAttribute('aria-expanded', 'false');
            el('url').focus();
            refresh();
        } catch (err) {
            setMsg(err.message);
        }
    });

    el('quality').addEventListener('change', function () {
        updateNote();
        updateSubtitleToggle();
    });
    updateSubtitleToggle();

    el('select-time').addEventListener('click', async function () {
        var fields = el('time-range-fields');
        fields.hidden = !fields.hidden;
        el('select-time').setAttribute('aria-expanded', String(!fields.hidden));
        if (!fields.hidden) {
            var url = el('url').value.trim();
            if (!url) {
                el('time-range-status').textContent = 'Enter a video URL first.';
                el('timeline-controls').hidden = true;
                return;
            }
            await loadVideoDuration(url);
        }
    });

    el('start-time').addEventListener('input', function () { updateTimeline('start'); });
    el('end-time').addEventListener('input', function () { updateTimeline('end'); });
    el('start-time-text').addEventListener('change', function () { updateSliderFromManual('start'); });
    el('end-time-text').addEventListener('change', function () { updateSliderFromManual('end'); });

    el('clear').addEventListener('click', async function () {
        try { await api('/api/jobs/clear', { method: 'POST', body: '{}' }); } catch (err) { setMsg(err.message); }
        refresh();
    });

    var optionsDialog = el('options-dialog');
    var settingsMsg = el('settings-msg');
    el('open-options').addEventListener('click', async function () {
        settingsMsg.textContent = '';
        settingsMsg.hidden = true;
        try {
            var settings = await api('/api/settings');
            el('output-root-input').value = settings.output_root;
            el('max-concurrent-downloads').value = settings.max_concurrent_downloads;
            optionsDialog.showModal();
        } catch (err) {
            setMsg(err.message);
        }
    });

    el('browse-folder').addEventListener('click', async function () {
        var browse = el('browse-folder');
        browse.disabled = true;
        settingsMsg.textContent = '';
        settingsMsg.hidden = true;
        try {
            var settings = await api('/api/settings/browse', { method: 'POST', body: '{}' });
            if (settings.output_root) { el('output-root-input').value = settings.output_root; }
        } catch (err) {
            settingsMsg.textContent = err.message;
            settingsMsg.hidden = false;
        } finally {
            browse.disabled = false;
        }
    });

    var maxConcurrentInput = el('max-concurrent-downloads');
    el('increase-downloads').addEventListener('click', function () {
        maxConcurrentInput.stepUp();
    });
    el('decrease-downloads').addEventListener('click', function () {
        if (maxConcurrentInput.value === '') {
            maxConcurrentInput.value = maxConcurrentInput.min || '1';
        } else {
            maxConcurrentInput.stepDown();
        }
    });

    function closeOptions() { optionsDialog.close(); }
    el('close-options').addEventListener('click', closeOptions);
    el('cancel-options').addEventListener('click', closeOptions);
    el('settings-form').addEventListener('submit', async function (e) {
        e.preventDefault();
        var save = el('save-settings');
        save.disabled = true;
        settingsMsg.textContent = '';
        settingsMsg.hidden = true;
        try {
            var settings = await api('/api/settings', {
                method: 'POST',
                body: JSON.stringify({
                    output_root: el('output-root-input').value,
                    max_concurrent_downloads: Number(el('max-concurrent-downloads').value)
                })
            });
            el('output-root').textContent = settings.output_root;
            closeOptions();
        } catch (err) {
            settingsMsg.textContent = err.message;
            settingsMsg.hidden = false;
        } finally {
            save.disabled = false;
        }
    });

    updateNote();
    refresh();
    setInterval(refresh, 1000);
})();
