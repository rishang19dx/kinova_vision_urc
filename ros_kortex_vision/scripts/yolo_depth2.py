#!/usr/bin/env python

import rospy
from sensor_msgs.msg import Image
from cv_bridge import CvBridge
import cv2
import torch
import numpy as np

class YOLOv5ROSNode:
    def __init__(self):
        # Initialize ROS node
        rospy.init_node('yolov5_ros_node', anonymous=True)

        # Load YOLOv5 model
        self.model = torch.hub.load('ultralytics/yolov5', 'yolov5s', pretrained=True)

        # Initialize CV Bridge
        self.bridge = CvBridge()

        # Subscribe to RGB and Depth camera topics
        self.image_sub = rospy.Subscriber("/camera/color/image_raw", Image, self.image_callback)
        self.depth_sub = rospy.Subscriber("/camera/depth/image_raw", Image, self.depth_callback)

        # Publisher for annotated image
        self.image_pub = rospy.Publisher("/yolo/detected_image", Image, queue_size=10)

        # Store latest depth image
        self.depth_image = None
        self.depth_encoding = None

    def depth_callback(self, msg):
        """ Stores the latest depth image """
        try:
            self.depth_encoding = msg.encoding  # Check depth format
            rospy.loginfo(f"Depth image encoding: {self.depth_encoding}")

            if self.depth_encoding == "16UC1":
                self.depth_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="16UC1") * 0.001  # Convert mm to meters
            elif self.depth_encoding == "32FC1":
                self.depth_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="32FC1")
            else:
                rospy.logwarn(f"Unsupported depth encoding: {self.depth_encoding}")
                self.depth_image = None

            rospy.loginfo(f"Depth image shape: {self.depth_image.shape}")

        except Exception as e:
            rospy.logerr(f"Error processing depth image: {e}")
            self.depth_image = None

    def project_rgb_to_depth(self, center_x_rgb, center_y_rgb):
        """ Projects an RGB pixel coordinate to the depth camera frame. """

        # Extract intrinsic parameters for RGB camera
        fx_rgb, fy_rgb = 1297.67, 1298.63
        cx_rgb, cy_rgb = 620.91, 238.28

        # Extract intrinsic parameters for Depth camera
        fx_depth, fy_depth = 360.01, 360.01
        cx_depth, cy_depth = 243.87, 137.92

        # Get depth value at corresponding pixel location
        if self.depth_image is None:
            return -1, -1, -1  # No depth data

        if 0 <= center_x_rgb < self.depth_image.shape[1] and 0 <= center_y_rgb < self.depth_image.shape[0]:
            Z = self.depth_image[center_y_rgb, center_x_rgb]  # Depth in meters
        else:
            return -1, -1, -1  # Invalid depth

        if Z > 0:  # If valid depth
            # Convert to 3D world coordinates using RGB camera intrinsics
            X = (center_x_rgb - cx_rgb) * Z / fx_rgb
            Y = (center_y_rgb - cy_rgb) * Z / fy_rgb

            # Reproject to depth camera pixel coordinates
            center_x_depth = int((X * fx_depth / Z) + cx_depth)
            center_y_depth = int((Y * fy_depth / Z) + cy_depth)

            return center_x_depth, center_y_depth, Z
        else:
            return -1, -1, -1  # Invalid depth

    def image_callback(self, msg):
        try:
            # Convert ROS Image to OpenCV format
            cv_image = self.bridge.imgmsg_to_cv2(msg, "bgr8")
            
            rospy.loginfo(f"RGB image shape: {cv_image.shape}")

            # Perform object detection
            results = self.model(cv_image)

            # Render detections on the RGB image
            rendered_image = cv_image.copy()

            if self.depth_image is not None:
                min_depth = np.min(self.depth_image)
                max_depth = np.max(self.depth_image)
                rospy.loginfo(f"Depth Range: {min_depth:.2f}m to {max_depth:.2f}m")

                # Normalize and apply colormap to depth image for visualization
                depth_vis = cv2.normalize(self.depth_image, None, 0, 255, cv2.NORM_MINMAX)
                depth_vis = np.uint8(depth_vis)
                depth_vis_colored = cv2.applyColorMap(depth_vis, cv2.COLORMAP_JET)

            for *xyxy, conf, cls in results.xyxy[0]:  # Bounding box coordinates
                if int(cls) != 41:  # Only process "cup"
                    continue

                x1, y1, x2, y2 = map(int, xyxy)
                center_x_rgb, center_y_rgb = (x1 + x2) // 2, (y1 + y2) // 2

                # Convert to depth camera coordinates
                center_x_depth, center_y_depth, depth_value = self.project_rgb_to_depth(center_x_rgb, center_y_rgb)

                if center_x_depth != -1 and center_y_depth != -1:
                    rospy.loginfo(f"Cup detected at RGB({center_x_rgb}, {center_y_rgb}) -> Depth({center_x_depth}, {center_y_depth}), Depth: {depth_value:.2f}m")

                    # Draw bounding box in RGB image
                    cv2.rectangle(rendered_image, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    cv2.circle(rendered_image, (center_x_rgb, center_y_rgb), 5, (255, 0, 0), -1)
                    cv2.putText(rendered_image, f"{depth_value:.2f}m", (x2 - 60, y2 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

                    # Draw bounding box in Depth Image
                    cv2.rectangle(depth_vis_colored, (center_x_depth - 10, center_y_depth - 10), 
                                  (center_x_depth + 10, center_y_depth + 10), (0, 255, 255), 2)
                    cv2.circle(depth_vis_colored, (center_x_depth, center_y_depth), 5, (255, 0, 0), -1)
                    cv2.putText(depth_vis_colored, f"{depth_value:.2f}m", (center_x_depth, center_y_depth - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)

            # Convert and publish annotated image
            annotated_image_msg = self.bridge.cv2_to_imgmsg(rendered_image, "bgr8")
            self.image_pub.publish(annotated_image_msg)

            # Display the annotated images
            cv2.imshow("YOLOv5 Detection - Cups Only", rendered_image)
            cv2.imshow("Depth Image with Detections", depth_vis_colored)
            cv2.waitKey(1)

        except Exception as e:
            rospy.logerr(f"Error processing image: {e}")


if __name__ == '__main__':
    try:
        yolo_node = YOLOv5ROSNode()
        rospy.spin()
    except rospy.ROSInterruptException:
        pass
