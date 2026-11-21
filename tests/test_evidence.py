"""Synthetic video, text and timing tests. No production account or source dependence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from common import DEFAULTS, atomic_json, load_json, binary, exclusive
from analyzer import run_media, error_label
from evidence import (align, changed, tiles_for_size, transcript_quality, build_evidence,
                      record_review, update_manifest, check_audio_alignment, build_packet, merge_caption_text)


class EvidenceTests(unittest.TestCase):
    def test_windows_file_race_and_stop_restart(self):
        from common import atomic_text
        import common, app
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            packet=Path(tmp);(packet/'evidence').mkdir()
            file=packet/'progress.json';atomic_text(file,'original')
            replace=common.os.replace
            calls=[]
            def transient(a,b):
                calls.append(1)
                if len(calls)<3:raise PermissionError('Windows sharing race')
                replace(a,b)
            with patch.object(common.os,'replace',transient):atomic_text(file,'updated')
            self.assertEqual(file.read_text(),'updated')
            with patch.object(common.os,'replace',side_effect=PermissionError('locked')),patch.object(common.time,'sleep'):
                with self.assertRaises(PermissionError):atomic_text(file,'lost')
            self.assertEqual(file.read_text(),'updated')
            self.assertFalse(list(packet.glob('*.tmp')))
            ui=app.Workbench.__new__(app.Workbench)
            ui.analysis_process=None;ui.session_processors=[];ui.evidence_packet=packet
            ui.evidence_queue=[];ui.evidence_stopping=False
            ui.status=SimpleNamespace(set=lambda value:None)
            ui.packet_tree=SimpleNamespace(selection=lambda:['video'])
            ui.packet_entries={'video':{'path':str(packet)}}
            ui.stop_reading();ui.build_reading();ui.build_reading()
            self.assertEqual(ui.evidence_queue,[packet])
            self.assertTrue((packet/'evidence'/'STOP').exists())
            ready=packet/'evidence'/'reader-ready.html';ready.write_text('completed reader')
            atomic_json(packet/'evidence'/'status.json',{'status':'interrupted'})
            with patch.object(app.os,'startfile',create=True) as opened:
                ui.open_reading()
                opened.assert_called_once_with(str(ready))

    def test_active_reading_blocks_library_mutations(self):
        from projects import create_project, move_entry, remove_entry
        from common import new_job
        from analyzer import save_job
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);settings={**DEFAULTS,'output_dir':str(root/'library')}
            source=root/'fixture.mp4';source.write_bytes(b'fixture')
            first=create_project(settings,'First');second=create_project(settings,'Second')
            job=new_job(str(source),settings,project_id=first.name)
            packet=Path(job['packet_path']);packet.mkdir(parents=True)
            atomic_json(packet/'packet.json',{'packet_id':job['id'],'status':'complete'})
            save_job(job,'complete')
            with exclusive(packet/'.evidence.lock'):
                with self.assertRaises(ValueError):move_entry(settings,packet,second.name)
                with self.assertRaises(ValueError):remove_entry(settings,packet)
            self.assertTrue(packet.is_dir())
            destination=move_entry(settings,packet,second.name)
            self.assertTrue(destination.is_dir())

    def test_local_change_and_crop_coverage(self):
        blank = np.zeros((180,320), dtype=np.uint8)
        text = blank.copy(); text[20:35,40:55]=255
        self.assertTrue(changed(blank,text))
        self.assertFalse(changed(blank,blank))
        for w,h in [(1920,1080),(1080,1920),(638,360),(3840,2160)]:
            coverage=np.zeros((h,w),dtype=bool)
            for x,y,r,b in tiles_for_size(w,h):
                self.assertLessEqual(r-x,1000);self.assertLessEqual(b-y,1000)
                coverage[y:b,x:r]=True
            self.assertTrue(coverage.all())

    def test_alignment_transition_and_quality(self):
        self.assertEqual(merge_caption_text(['Revenue grew by','grew by ten percent','ten percent']),'Revenue grew by ten percent')
        segments=[{'start':.5,'end':1.5,'text':'crossing','avg_logprob':-1,'words':[{'text':'uncertain','probability':.2}]}]
        states=[{'id':'a','start':0,'end':1},{'id':'b','start':1,'end':2}]
        mapping=[{'file':'first.jpg','timestamp_seconds':0},{'file':'second.jpg','timestamp_seconds':1}]
        row=align(segments,states,mapping)[0]
        self.assertEqual(row['screen_ids'],['a','b'])
        self.assertEqual(row['nearest_sample']['delta_seconds'],0)
        quality=transcript_quality({'segments':segments},{'levels':[{'start':n,'end':n+1,'rms_dbfs':-20} for n in range(10)]},10)
        self.assertEqual(len(quality['segment_flags']),1)
        self.assertEqual(len(quality['audible_transcript_gaps']),1)
        self.assertFalse(quality['verified'])
        self.assertEqual(error_label('Sign in to confirm you are not a bot; use cookies'),'access_blocked')
        self.assertEqual(error_label('Private video. Sign in required'),'sign_in_required')
        calibrated=align(segments,states,mapping,.2)[0]
        self.assertAlmostEqual(calibrated['start'],.7)
        self.assertEqual(calibrated['asr_start'],.5)

    def test_audio_offset_detection_and_removed_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            t=np.arange(16000*8)/16000
            rng=np.random.default_rng(14)
            noise=np.convolve(rng.normal(size=len(t)),np.ones(12)/12,mode='same')
            signal=(noise*12000).astype('<i2')
            wav=root/'audio.wav'
            with wave.open(str(wav),'wb') as stream:
                stream.setnchannels(1);stream.setsampwidth(2);stream.setframerate(16000);stream.writeframes(signal.tobytes())
            check=check_audio_alignment(wav,wav,8)
            self.assertEqual(check['status'],'matched')
            self.assertTrue(all(abs(o['offset_seconds'])<.01 for o in check['landmarks']))
            delayed=root/'delayed.wav'
            run_media([binary('ffmpeg'),'-y','-i',str(wav),'-af','adelay=300',str(delayed)])
            self.assertEqual(check_audio_alignment(delayed,wav,8)['status'],'needs_attention')
            missing=root/'removed-packet'
            with self.assertRaises(FileNotFoundError):build_packet(missing)
            self.assertFalse(missing.exists())

    def test_video_text_timing_reader_cache_and_review(self):
        with tempfile.TemporaryDirectory(prefix='workbench-readable-') as tmp:
            packet=Path(tmp); settings={**DEFAULTS,'output_dir':str(packet/'library')}
            (packet/'source').mkdir();(packet/'audio').mkdir();(packet/'visuals'/'frames').mkdir(parents=True)
            font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',22)
            images=[]
            for title,value in [('Revenue 427.50','First period'),('Revenue 913.25','Second period')]:
                image=Image.new('RGB',(1920,1080),'white');draw=ImageDraw.Draw(image)
                draw.text((250,230),title,font=font,fill='black');draw.text((1250,800),value,font=font,fill='black')
                images.append(image)
            first=packet/'first.png'; second=packet/'second.png';images[0].save(first);images[1].save(second)
            video=packet/'source'/'video.mp4'
            run_media([binary('ffmpeg'),'-y','-loop','1','-framerate','10','-t','2','-i',str(first),'-loop','1','-framerate','10','-t','2','-i',str(second),'-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-r','10','-c:v','libx264','-pix_fmt','yuv420p',str(video)])
            frames=[]
            for i in range(8):
                path=packet/'visuals'/'frames'/f'{i}.png'; images[i//4].resize((960,540)).save(path)
                frames.append({'file':'frames/'+path.name,'timestamp_seconds':i*.5})
            atomic_json(packet/'visuals'/'frames.json',{'frames':frames})
            atomic_json(packet/'visuals'/'coverage.json',{'full_timeline_sampled':True})
            transcript={'full_audio_processed':True,'segments':[{'start':.2,'end':1.8,'text':'Revenue in the first period'},{'start':1.9,'end':2.8,'text':'Across the screen transition </script><script>window.pwned=true</script>'}]}
            atomic_json(packet/'audio'/'transcript.json',transcript)
            atomic_json(packet/'packet.json',{'packet_id':'fixture','status':'complete','duration_seconds':4,'source':{'title':'Fixture','url_or_file':str(video)},'processing_settings':settings})
            status=build_evidence(packet)
            update_manifest(packet)
            self.assertEqual(status['status'],'ready')
            screens=load_json(packet/'evidence'/'screens.json')['screens']
            self.assertEqual(len(screens),2)
            self.assertIn('427.50',screens[0]['ocr_text'])
            self.assertIn('913.25',screens[1]['ocr_text'])
            self.assertEqual(screens[0]['source_dimensions'],[1920,1080])
            self.assertLess(abs(screens[1]['image_timestamp']-2),.11)
            alignment=load_json(packet/'evidence'/'alignment.json')['segments']
            self.assertEqual(len(alignment[1]['screen_ids']),2)
            self.assertLessEqual(abs(alignment[1]['nearest_sample']['delta_seconds']),.25)
            with patch('evidence.recognize',side_effect=AssertionError('OCR cache not reused')):
                repeated=build_evidence(packet)
            self.assertEqual(repeated['evidence_id'],status['evidence_id'])
            self.assertEqual(load_json(packet/'packet.json')['review_status'],'unreviewed')
            ids=[s['id'] for s in screens]
            record_review(packet,[ids[0]],'synthetic test',True,True)
            self.assertEqual(load_json(packet/'review.json')['status'],'partial')
            record_review(packet,[ids[1]],'synthetic test',True,True)
            self.assertEqual(load_json(packet/'review.json')['status'],'reviewed')
            with self.assertRaises(ValueError):record_review(packet,['nonexistent'],'fixture',True,True)
            transcript['segments'][0]['text']='Changed transcript'
            atomic_json(packet/'audio'/'transcript.json',transcript)
            build_evidence(packet);update_manifest(packet)
            self.assertEqual(load_json(packet/'packet.json')['review_status'],'unreviewed')
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                page=browser.new_page(viewport={'width':1366,'height':1000})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto((packet/'evidence'/'reader.html').as_uri())
                self.assertEqual(page.locator('#screens .row').count(),2)
                self.assertEqual(page.locator('#passages .row').count(),2)
                self.assertFalse(page.evaluate('Boolean(window.pwned)'))
                page.locator('#next').click()
                self.assertEqual(page.locator('#screenTitle').inner_text(),ids[1])
                page.locator('#audioButton').click()
                self.assertTrue(page.locator('#video').is_hidden())
                self.assertTrue(page.locator('#audio').is_visible())
                page.locator('#search').fill('427.50')
                self.assertEqual(page.locator('#screens .row').count(),1)
                page.locator('#search').fill('no-match-string')
                self.assertTrue(page.locator('#empty').is_visible())
                self.assertEqual(errors,[])
                browser.close()


if __name__=='__main__':unittest.main(verbosity=2)
