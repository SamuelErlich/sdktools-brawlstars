#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN
/// Copies only five pinned static tables. Call on a utility queue, never main.
@interface SDKAssetExport : NSObject
+ (NSDictionary *)exportTablesToDirectory:(NSURL *)directory;
@end
NS_ASSUME_NONNULL_END
