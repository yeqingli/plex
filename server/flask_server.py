import os
import pdb
from flask import Flask, request, redirect, url_for, jsonify, make_response, render_template
from werkzeug import secure_filename
from flask import send_from_directory
import urllib

from pano import PanoMap        

import matplotlib as mpl
mpl.use('Agg')
import matplotlib.pyplot as plt
import cv2

import sys

import cPickle
import settings # grab server directory's settings, not python directory
sys.path.append(settings.libsvm_path)
import svmutil as svm

sys.path.append(os.path.join('..','python'))
from display import DrawWordResults, DebugCharBbs
from wordspot import WordSpot

with open(settings.char_clf_name,'rb') as fid:
    rf = cPickle.load(fid)
print 'Pre-loaded character classifier'

with open(settings.word_clf_meta_name,'rb') as fid:
    alpha_min_max = cPickle.load(fid)
alpha = alpha_min_max[0]
min_max = (alpha_min_max[1], alpha_min_max[2])
svm_clf = svm.svm_load_model(settings.word_clf_name)
svm_model=(svm_clf,min_max)

app = Flask(__name__)
app.config['CTRL_F_UPLOAD_FOLDER'] = '/var/www/plex'
app.config['SVT_UPLOAD_FOLDER'] = '/var/www/svt'
app.debug = False


"""
Copied from stack overflow:
http://stackoverflow.com/questions/13768007/browser-caching-issues-in-flask
"""
@app.after_request
def add_header(response):
    """
    Add headers to both force latest IE rendering engine or Chrome Frame,
    and also to cache the rendered page for 10 minutes.
    """
    response.headers['X-UA-Compatible'] = 'IE=Edge,chrome=1'
    response.headers['Cache-Control'] = 'public, max-age=0'
    return response

@app.route('/')
def hello_world():
    return 'Hello World!'

# -----------------------
# -- STREETVIEW VIEWER --
# -----------------------
@app.route('/svt_viewer', methods=['GET'])
def hello_svt():
    return render_template('svt_viewer.html')

@app.route('/svt_result', methods=['GET'])
def show_result():
    orig_image = request.args['orig_image']
    result_image = request.args['result_image']
    #return_link = request.args['return_link'] -- TODO add this
    return render_template('result.html', orig_image=orig_image, result_image=result_image)

@app.route('/svt_listener', methods=['GET', 'POST'])
def svt_listener_url():

    zoom_to_hfov = {'1':90, '2':45, '3':25, '4':15}

    pano_id = request.form['pano']
    car_yaw = float(request.form['car-yaw'])
    img_width = int(request.form['width'])
    img_height = int(request.form['height'])
    pitch = float(request.form['pitch'])
    yaw = float(request.form['yaw'])
    print request.form['zoom']
    hfov = zoom_to_hfov[request.form['zoom']] # TODO, map this from 'zoom'

    pano_map = PanoMap(pano_id, car_yaw, app.config['SVT_UPLOAD_FOLDER'])
    img_cutout_filename = pano_map.cutout(img_width, img_height, pitch, yaw, hfov, override=1)
    img_result_filename = img_cutout_filename + '_result.jpg'
    # call SWT+TESS for now
    
    print 'business search result: ', request.form['business-text']
    SwtAndTesseract(img_cutout_filename, img_result_filename)
    
    params = urllib.urlencode({'orig_image': url_for('svt_viewer_file', filename=os.path.basename(img_cutout_filename)), 'result_image': url_for('svt_viewer_file', filename=os.path.basename(img_result_filename))})
    result_page = "/svt_result?%s" % params
    r1 = {'result_url': result_page}
    return jsonify(r1)


# ----------------------
# -- CTRL-F INTERFACE --
# ----------------------
@app.route('/ctrl_f', methods=['GET', 'POST'])
def upload_url():

    if request.method == 'POST':
        img_url= request.form['img_url']
        string_to_find = request.form['string_to_find']
        num_instances = int(request.form['num_instances'])
        filename = os.path.basename(img_url)
        full_filename = os.path.join(app.config['CTRL_F_UPLOAD_FOLDER'], filename)

        urllib.urlretrieve(img_url, full_filename)

        result_filename = filename+'_result.png'
        full_result_filename = os.path.join(app.config['CTRL_F_UPLOAD_FOLDER'],
                                            result_filename)
        WebWordspot(full_filename, [string_to_find], num_instances,
                    full_result_filename, rf, alpha, svm_model=svm_model)

        params = urllib.urlencode({'orig_image': url_for('uploaded_file', filename=filename),
                                   'result_image': url_for('uploaded_file', filename=result_filename),
                                   'return_link': 'ctrl_f',
                                   'notes':'try hitting F5 if the result is weird'})
        result_page = "/svt_result?%s" % params

        return redirect(result_page)

    return '''
    <!doctype html>
    <title>Wordspotting demo</title>
    <h1>CTRL-F for images</h1>
    <form action="" method=post>
    <p>Search string: <input type=text name="string_to_find">
    # instances to find: <input type=text name="num_instances">
    <p><input type=text name="img_url">
    <input type=submit value="Run CTRL-F">
    </form>
    '''


@app.route('/libccv_swt', methods=['GET', 'POST'])
def upload_swt():
    if request.method == 'POST':
        img_url= request.form['img_url']
        filename = os.path.basename(img_url)
        full_filename = os.path.join(app.config['CTRL_F_UPLOAD_FOLDER'], filename)
        urllib.urlretrieve(img_url, full_filename)
        result_filename = filename+'_swt.png'
        full_result_filename = os.path.join(app.config['CTRL_F_UPLOAD_FOLDER'],
                                            result_filename)

        SwtAndTesseract(full_filename, full_result_filename)

        return redirect(url_for('uploaded_file',
                                filename=result_filename))

    return '''
    <!doctype html>
    <title>libccv-SWT demo</title>
    <h1>Display output of libccv-swt text detector</h1>
    <form action="" method=post>
    <p><input type=text name="img_url">
    <input type=submit value="Run SWT">
    </form>
    '''

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['CTRL_F_UPLOAD_FOLDER'],
                               filename)

@app.route('/svt/<filename>')
def svt_viewer_file(filename):
    return send_from_directory(app.config['SVT_UPLOAD_FOLDER'],
                               filename)

# ----------------------
# HELPER FUNCTIONS
# ----------------------
def SwtAndTesseract(full_filename, full_result_filename):
    swt_dir = settings.swt_dir
    swt_app = os.path.join(swt_dir, 'swtdetect')
    swtdraw_app = "tesseract_process.py"
    cmd = "%s %s | python %s %s %s" % (swt_app, full_filename,
                                       swtdraw_app, full_filename,
                                       full_result_filename)
    os.system(cmd)    

def WebWordspot(img_name, lexicon, max_locations, result_path, rf, alpha, svm_model=None,
                debug_img_name=None):
    img = cv2.imread(img_name)    
    (match_bbs, char_bbs) = WordSpot(img, lexicon, alpha, settings, use_cache=False,
                                     img_name=img_name, max_locations=max_locations,
                                     svm_model=svm_model, rf_preload=rf)

    DrawWordResults(img, match_bbs)
    plt.savefig(result_path)

    DebugCharBbs(img, char_bbs, settings.alphabet_master, lexicon)
    debug_path = result_path+'_dbg.png' 
    plt.savefig(debug_path)
    return debug_path

if __name__ == '__main__':
    app.run(host='0.0.0.0')
