#import <Foundation/Foundation.h>
#import <UIKit/UIKit.h>

NS_ASSUME_NONNULL_BEGIN

/// Passive, user-initiated capture. All public operations run on the main thread.
@interface SDKCaptureManager : NSObject
+ (instancetype)shared;
@property(nonatomic, readonly) BOOL recording;
@property(nonatomic, copy, readonly) NSString *statusText;
- (void)startCapture;
- (void)captureManualSnapshot;
- (void)recordMarker;
- (void)stopCapture;
- (void)exportCaptureFromViewController:(UIViewController *)presenter sourceView:(UIView *)sourceView;
@end

NS_ASSUME_NONNULL_END
