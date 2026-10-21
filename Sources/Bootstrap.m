#import <UIKit/UIKit.h>
#import <dispatch/dispatch.h>
#import "SDKCaptureOverlay.h"

@interface SDKCaptureBootstrap : NSObject
@property(nonatomic, strong) SDKCaptureOverlay *overlay;
@property(nonatomic, strong) NSMutableArray<id> *notificationTokens;
- (void)install;
@end

static BOOL SDKBootstrapTargetMatches(void) {
    NSBundle *module = [NSBundle bundleForClass:SDKCaptureBootstrap.class];
    NSURL *url = [module URLForResource:@"Target" withExtension:@"json"];
    if (!url) return NO;
    NSNumber *size = nil;
    if (![url getResourceValue:&size forKey:NSURLFileSizeKey error:nil] || size.unsignedLongLongValue > 65536) return NO;
    NSData *data = [NSData dataWithContentsOfURL:url options:NSDataReadingMappedIfSafe error:nil];
    if (!data || data.length > 65536) return NO;
    id root = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![root isKindOfClass:NSDictionary.class] || ![root[@"schema_version"] isEqual:@1]) return NO;
    id target = root[@"target"];
    if (![target isKindOfClass:NSDictionary.class]) return NO;
    for (NSString *key in @[@"bundle_identifier", @"version", @"build"])
        if (![target[key] isKindOfClass:NSString.class] || [target[key] length] == 0) return NO;
    NSBundle *app = NSBundle.mainBundle;
    return [app.bundleIdentifier isEqualToString:target[@"bundle_identifier"]] &&
        [[app objectForInfoDictionaryKey:@"CFBundleShortVersionString"] isEqual:target[@"version"]] &&
        [[app objectForInfoDictionaryKey:@"CFBundleVersion"] isEqual:target[@"build"]];
}

@implementation SDKCaptureBootstrap
- (void)install {
    self.overlay = [[SDKCaptureOverlay alloc] init];
    self.notificationTokens = [NSMutableArray array];
    __weak typeof(self) weakSelf = self;
    NSArray<NSNotificationName> *names = @[
        UIApplicationDidFinishLaunchingNotification,
        UIApplicationDidBecomeActiveNotification,
        UIApplicationWillResignActiveNotification,
        UIApplicationDidEnterBackgroundNotification,
        UISceneDidActivateNotification,
        UISceneWillDeactivateNotification,
        UISceneDidDisconnectNotification,
        UIWindowDidBecomeVisibleNotification
    ];
    for (NSNotificationName name in names) {
        id token = [NSNotificationCenter.defaultCenter addObserverForName:name object:nil queue:NSOperationQueue.mainQueue usingBlock:^(NSNotification *note) {
            if ([note.name isEqualToString:UIWindowDidBecomeVisibleNotification] &&
                [NSStringFromClass([note.object class]) hasPrefix:@"SDK"]) return;
            dispatch_async(dispatch_get_main_queue(), ^{ [weakSelf.overlay refreshAttachment]; });
        }];
        [self.notificationTokens addObject:token];
    }
    [self.overlay refreshAttachment];
}
- (void)dealloc {
    for (id token in _notificationTokens) [NSNotificationCenter.defaultCenter removeObserver:token];
}
@end

__attribute__((constructor)) static void SDKCaptureLoad(void) {
    dispatch_async(dispatch_get_main_queue(), ^{
        if (!SDKBootstrapTargetMatches()) return;
        // UUID and exact runtime identity are independently checked by Manager
        // before recording. Bootstrap never starts a session automatically.
        static SDKCaptureBootstrap *bootstrap;
        static dispatch_once_t once;
        dispatch_once(&once, ^{
            bootstrap = [[SDKCaptureBootstrap alloc] init];
            [bootstrap install];
        });
    });
}
