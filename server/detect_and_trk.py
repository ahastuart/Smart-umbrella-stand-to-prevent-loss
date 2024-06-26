import hydra
import torch
import cv2
import queue
from random import randint
from sort import *
from ultralytics.yolo.engine.predictor import BasePredictor
from ultralytics.yolo.utils import DEFAULT_CONFIG, ROOT, ops
from ultralytics.yolo.utils.checks import check_imgsz
from ultralytics.yolo.utils.plotting import Annotator, colors, save_one_box
from store_images_and_data import store_matched_data

tracker = None
global umbrella_in_stand
umbrella_in_stand = None
umbrella_queue = queue.Queue()
checkout_queue = queue.Queue()

def init_tracker():
    global tracker
    
    sort_max_age = 5 
    sort_min_hits = 2
    sort_iou_thresh = 0.2
    tracker =Sort(max_age=sort_max_age,min_hits=sort_min_hits,iou_threshold=sort_iou_thresh)

rand_color_list = []
    
def draw_boxes(img, bbox, identities=None, categories=None, names=None, offset=(0, 0)):

    for i, box in enumerate(bbox):
        x1, y1, x2, y2 = [int(i) for i in box]
        x1 += offset[0]
        x2 += offset[0]
        y1 += offset[1]
        y2 += offset[1]
        id = int(identities[i]) if identities is not None else 0
        box_center = (int((box[0]+box[2])/2),(int((box[1]+box[3])/2)))
        label = str(id)
        (w, h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 253), 2)
        cv2.rectangle(img, (x1, y1 - 20), (x1 + w, y1), (255,144,30), -1)
        cv2.putText(img, label, (x1, y1 - 5),cv2.FONT_HERSHEY_SIMPLEX, 0.6, [255, 255, 255], 1)
        
        
    return img

def random_color_list():
    global rand_color_list
    rand_color_list = []
    for i in range(0,5005):
        r = randint(0, 255)
        g = randint(0, 255)
        b = randint(0, 255)
        rand_color = (r, g, b)
        rand_color_list.append(rand_color)
    #......................................
     

class DetectionPredictor(BasePredictor):
        
    # counter bounding box
    counter_bbox = (400, 100, 600, 600)  

    def is_inside_counter_bbox(self, bbox):
        cx = (bbox[0] + bbox[2]) // 2
        cy = (bbox[1] + bbox[3]) // 2
        if self.counter_bbox[0] <= cx <= self.counter_bbox[2] and self.counter_bbox[1] <= cy <= self.counter_bbox[3]:
            return True
        return False
    
    def get_annotator(self, img):
        return Annotator(img, line_width=self.args.line_thickness, example=str(self.model.names))

    def preprocess(self, img):
        img = torch.from_numpy(img).to(self.model.device)
        img = img.half() if self.model.fp16 else img.float()  # uint8 to fp16/32
        img /= 255  # 0 - 255 to 0.0 - 1.0
        return img

    def postprocess(self, preds, img, orig_img):
        preds = ops.non_max_suppression(preds,
                                        self.args.conf,
                                        self.args.iou,
                                        agnostic=self.args.agnostic_nms,
                                        max_det=self.args.max_det)

        for i, pred in enumerate(preds):
            shape = orig_img[i].shape if self.webcam else orig_img.shape
            pred[:, :4] = ops.scale_boxes(img.shape[2:], pred[:, :4], shape).round()

        return preds

    def write_results(self, idx, preds, batch):
        global umbrella_in_stand

        p, im, im0 = batch
        log_string = ""
        if len(im.shape) == 3:
            im = im[None]  # expand for batch dim
        self.seen += 1
        im0 = im0.copy()
        if self.webcam:  # batch_size >= 1
            log_string += f'{idx}: '
            frame = self.dataset.count
        else:
            frame = getattr(self.dataset, 'frame', 0)
        # tracker
        self.data_path = p

        save_path = str(self.save_dir / p.name)  # im.jpg
        self.txt_path = str(self.save_dir / 'labels' / p.stem) + ('' if self.dataset.mode == 'image' else f'_{frame}')
        log_string += '%gx%g ' % im.shape[2:]  # print string
        self.annotator = self.get_annotator(im0)
        
        det = preds[idx]
        self.all_outputs.append(det)
        if len(det) == 0:
            return log_string
            
            
        # detect and tracking (umbrella,person)
        umbrella_dets = []
        person_dets = []
        umbrella_id = 0
        person_id = 0
        for *xyxy, conf, cls in det:
            if self.model.names[int(cls)] == 'umbrella':
                umbrella_dets.append({'bbox': xyxy, 'conf': conf, 'id': umbrella_id})
                umbrella_id += 1
                umbrella_in_stand = umbrella_id
            elif self.model.names[int(cls)] == 'person':
                person_dets.append({'bbox': xyxy, 'conf': conf, 'id': person_id})
                person_id += 1
        
        # center coordinate
        for umbrella in umbrella_dets:
            x1, y1, x2, y2 = umbrella['bbox']
            umbrella['center'] = (int((x1 + x2) / 2), int((y1 + y2) / 2))
        
        for person in person_dets:
            x1, y1, x2, y2 = person['bbox']
            person['center'] = (int((x1 + x2) / 2), int((y1 + y2) / 2))
       
        # Call match_umbrella_person function
        from match_umbrella_person import match_umbrella_person
        matched_pairs = match_umbrella_person(umbrella_dets, person_dets)
        
        # Visualize by drawing a line between a matching umbrella and a person
        for umbrella, person in matched_pairs:
            ux, uy = umbrella['center']
            px, py = person['center']
            cv2.line(im0, (ux, uy), (px, py), (0, 255, 0), 2)
            log_string += f"Matched: Umbrella {ux, uy} - Person {px, py}, "

        for umbrella, person in matched_pairs:
            ux1, uy1, ux2, uy2 = map(int, umbrella['bbox'])
            px1, py1, px2, py2 = map(int, person['bbox'])
        
            umbrella_img = im0[uy1:uy2, ux1:ux2]
            person_img = im0[py1:py2, px1:px2]
        
            umbrella_img_path = f"umbrella_{umbrella['id']}.jpg"
            person_img_path = f"person_{person['id']}.jpg"
        
            cv2.imwrite(umbrella_img_path, umbrella_img)
            cv2.imwrite(person_img_path, person_img)
        
            match_data = {
                'umbrella_id': str(umbrella['id']),
                'person_id': str(person['id']),
                'umbrella_img_path': umbrella_img_path,
                'person_img_path': person_img_path
            }
            store_matched_data(match_data)
            
        # umbrella_stand bounding box
        umbrella_stand_bbox = (200, 200, 300, 400)  
        umbrella_stand_x1, umbrella_stand_y1, umbrella_stand_x2, umbrella_stand_y2 = umbrella_stand_bbox
        cv2.rectangle(im0, (umbrella_stand_x1, umbrella_stand_y1), (umbrella_stand_x2, umbrella_stand_y2), (255, 0, 0), 2)
        
        for umbrella in umbrella_dets:
            x1, y1, x2, y2 = umbrella['bbox']
            umbrella['center'] = (int((x1 + x2) / 2), int((y1 + y2) / 2))
            ux, uy = umbrella['center']

            # In the bbox of umbrella_stand
            if umbrella_stand_x1 < ux < umbrella_stand_x2 and umbrella_stand_y1 < uy < umbrella_stand_y2:
                umbrella_queue.put(umbrella['id'])
                print(f"Umbrella {umbrella['id']} is in the umbrella stand.")
                break
        
        # Draw counter bounding box
        counter_bbox = (450, 100, 600, 400)
        counter_x1, counter_y1, counter_x2, counter_y2 = counter_bbox
        cv2.rectangle(im0, (counter_x1, counter_y1), (counter_x2, counter_y2), (0, 255, 255), 2)
    
        for person in person_dets:
            px, py = person['center']
            if counter_x1 < px < counter_x2 and counter_y1 < py < counter_y2:
                checkout_queue.put(person['id'])
                break
                

        dets_to_sort = np.empty((0, 6))
        for det in umbrella_dets + person_dets:
            x1, y1, x2, y2 = det['bbox']
            conf = det['conf']
            cls = 0 if det in umbrella_dets else 1
            dets_to_sort = np.vstack((dets_to_sort, np.array([x1, y1, x2, y2, conf, cls])))
        
        tracked_dets = tracker.update(dets_to_sort)
        tracks = tracker.getTrackers()
        
        for track in tracks:
            [cv2.line(im0, (int(track.centroidarr[i][0]),
                        int(track.centroidarr[i][1])), 
                        (int(track.centroidarr[i+1][0]),
                        int(track.centroidarr[i+1][1])),
                        rand_color_list[track.id], thickness=3) 
                        for i,_ in  enumerate(track.centroidarr) 
                            if i < len(track.centroidarr)-1 ] 
        

        if len(tracked_dets)>0:
            bbox_xyxy = tracked_dets[:,:4]
            identities = tracked_dets[:, 8]
            categories = tracked_dets[:, 4]
            draw_boxes(im0, bbox_xyxy, identities, categories, self.model.names)
           
        gn = torch.tensor(im0.shape)[[1, 0, 1, 0]]  # normalization gain whwh
        
        return log_string


@hydra.main(version_base=None, config_path=str(DEFAULT_CONFIG.parent), config_name=DEFAULT_CONFIG.name)
def predict(cfg):
    init_tracker()
    random_color_list()
        
    cfg.model = cfg.model or "yolov8n.pt"
    cfg.imgsz = check_imgsz(cfg.imgsz, min_dim=2)  # check image size
    cfg.source = cfg.source if cfg.source is not None else ROOT / "assets"
    predictor = DetectionPredictor(cfg)
    detected_objects = predictor()
    return detected_objects


if __name__ == "__main__":
    predict()
